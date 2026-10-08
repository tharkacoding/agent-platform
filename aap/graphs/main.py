"""MAIN AGENT — the manager. Orchestrator / planner / delegator / reviewer.

START → load_context → classify_intent ─┬─ chat ─────────────► respond (streamed) ───► remember → END
                                        ├─ clarify ──────────► (ask one question) ───► remember
                                        ├─ schedule ─────────► schedule_task ────────► remember
                                        ├─ manage ───────────► manage_agents ────────► remember
                                        └─ task / build ─► analyze_capabilities → discover_agents → plan_dag
plan_dag → refine_plan (dedupe, cycle-break, LLM critique)
refine_plan ─┬─ missing capability ─► build_agent ─► discover_agents (re-plan with the new agent)
          ├─ steps ─► risk_gate ─► dispatch ══Send══► run_step (parallel, one per ready step) ─► collect
          └─ none  ─► synthesize
collect ─┬─ more ready steps ══Send══► run_step
         └─ all done ─► review ─┬─ follow-ups & loops left ─► dispatch
                                └─ satisfied ─► synthesize → remember → END
"""
from __future__ import annotations

import json
import operator
import re
import uuid
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .. import config as cfg, registry
from ..core import effort as tiers
from ..core.sdk import archetype, get_ctx
from ..models import AgentTask, Plan, PlanStep

# ------------------------------------------------------------------------------------------------ state
def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class MainState(TypedDict, total=False):
    task: dict
    report: dict
    request: str
    llm_down: bool                            # every model failed during classification (outage) → honest failure
    history: list
    memory_hits: list
    intent: str
    effort: int                               # seriousness tier 0-4 → token/reasoning budget (core/effort.py)
    caps: list
    criteria: list
    candidates: list
    plan: dict
    reports: Annotated[dict, _merge]          # step_id -> Report dict (fan-in from parallel run_step)
    running: Annotated[list, operator.add]
    iterations: int
    builds: int
    build_failed: str
    review: dict
    final: str
    xstate: dict                              # shared task state (core/execstate.py): todo, done, failed, browser
    user_effort: bool                         # True = the user set the effort level (never changed by the AI)
    questions_asked: bool
    seed_question: str                        # unclear request: the question to ask as a card (never as text)
    modes: list                               # /slash modes chosen by the user for this request (aap/modes.py)
    force_ask: bool                           # a mode (e.g. /interview) wants question cards before the work
    outcome_sigs: list                        # fingerprint of each review round (stop when nearly identical)
    steer_seen: int                           # how many mid-run user messages the plan already reflects


SYSTEM_IDS = {"main_agent", "agent_builder", "registry_manager", "memory_agent", "scheduler_agent"}

# keyword → capability map used by the deterministic (offline/mock) path
CAP_HINTS = [
    (r"\b(analy[sz]e|statistic|data|dataset|csv|chart|trend|metrics?)\b", "data_analysis"),
    (r"\b(code|program|function|script|implement|refactor|app|website|api)\b", "coding"),
    (r"\b(python)\b", "python"), (r"\b(javascript|node|typescript)\b", "javascript"), (r"\breact\b", "react"),
    (r"\bandroid|kotlin\b", "android"), (r"\b(sql|database|schema)\b", "database"), (r"\bc\+\+|cpp\b", "cpp"),
    (r"\b(bug|debug|error|crash|fix)\b", "debugging"), (r"\breview\b", "code_review"),
    (r"\b(test|tests|testing|pytest)\b", "testing"),
    (r"\b(research|search|find out|look up|latest|news|compare)\b", "web_research"),
    (r"\b(summar|tl;?dr)", "summarization"), (r"\b(plan|roadmap|schedule my|organi[sz]e)\b", "planning"),
    (r"\b(write|essay|email|blog|article|letter|draft)\b", "writing"),
    (r"\b(teach|explain|lesson|learn)\b", "teaching"), (r"\b(quiz|questions|exam|mcq)\b", "question_generation"),
    (r"\b(curriculum|syllabus|course)\b", "curriculum"),
    (r"\b(codeforces|cses|leetcode|competitive|algorithm)\b", "competitive_programming"),
    (r"\b(browse|website|web page|open .*\.(com|org)|click|login|fill)\b", "browser_automation"),
    (r"\b(terminal|command|shell|run|execute|install)\b", "terminal"),
    (r"\b(screenshot|screen)\b", "screenshot"),
    (r"\b(document|pdf|docs|readme)\b", "document_research"), (r"\bgithub|repo(sitory)?\b", "github"),
]
BUILD_RE = re.compile(r"\b(create|build|make|need|want|design|generate)\b.{0,40}\b(an?|new|custom|my own)?\s*"
                      r"(\w+\s){0,4}agent\b", re.I)
SCHEDULE_RE = re.compile(r"\b(every|each)\s+(day|morning|evening|night|week|monday|tuesday|wednesday|thursday|"
                         r"friday|saturday|sunday|hour|month)|\bdaily\b|\bweekly\b|\bremind me\b", re.I)


def _agent_brief(a) -> dict:
    return {"id": a.id, "name": a.name, "description": a.description, "capabilities": a.capabilities,
            "type": a.type}


def _plan_event(ctx, plan: dict, reports: dict | None = None, running: list | None = None) -> None:
    reports = reports or {}
    steps = []
    for s in plan.get("steps", []):
        st = "pending"
        verified = False
        if s["id"] in reports:
            rs = reports[s["id"]].get("status")
            st = "failed" if rs == "failed" else ("done" if rs == "success" else "unverified")
            verified = rs == "success"
        elif running and s["id"] in running:
            st = "running"
        steps.append({**s, "status": st, "verified": verified})
    ctx.run.emit("plan", "main_agent", "info", plan.get("intent", "plan"), plan={**plan, "steps": steps})


# ------------------------------------------------------------------------------------------------ nodes
def load_context(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("load_context", "Recalling memory and conversation")
    task = AgentTask.model_validate(state["task"])
    history = []
    for c in task.context:
        if c.get("kind") == "history":
            history = c.get("messages", [])
    hits = []
    try:
        hits = ctx.memory.search(task.instruction, 5, scope="main_agent")   # global + manager notes
    except Exception:  # noqa: BLE001
        pass
    if hits:
        ctx.emit(f"Recalled {len(hits)} memories", memories=[h["text"] for h in hits])
    from ..core import execstate
    prior = execstate.from_context(task.context)        # resume: continue an existing task state
    profile = execstate.parse_profile(task.instruction)
    xs = prior or execstate.new_state(task.instruction, profile)
    if profile:
        xs["browser"]["profile"] = profile               # the user's explicit profile wins everywhere
        # an instruction applies to THIS request only — it becomes a lasting default only if the user says so
        if execstate.is_standing(task.instruction):
            from .. import settings as _settings
            _settings.update({"browser_profile": profile})
            execstate.record_lesson("user", "preference", f"browser profile: {profile} (set as default)")
            ctx.emit(f"Saved '{profile}' as your default browser profile")
        ctx.emit(f"Using browser profile: {profile}", profile=profile)
    # EXPERIENCE: check notes from previous similar tasks first (what to do / not to do) — no model call
    from ..core import experience
    try:
        exp_text, exp_ids = experience.recall(task.instruction)
    except Exception as e:  # noqa: BLE001 - a broken note must never stop a task
        exp_text, exp_ids = "", []
        ctx.emit(f"experience notes unreadable: {e}")
    if exp_text:
        xs["lessons"] = list(dict.fromkeys((xs.get("lessons") or []) + [ln[:300] for ln in exp_text.splitlines()]))
        ctx.emit(f"Checked {len(exp_ids)} experience note(s): " + ", ".join(exp_ids), experience=exp_text)
    # SELF-LEARNING: what went wrong last time on the sites this request names (post-mortems of earlier runs)
    from .. import site_aliases
    sites = site_aliases.domains_in(task.instruction)
    learned = execstate.lessons_for(sites + ["general"], 6) if sites else []
    if learned:
        xs["lessons"] = list(dict.fromkeys((xs.get("lessons") or []) + learned))
        ctx.emit(f"Recalled {len(learned)} lesson(s) from earlier runs on {', '.join(sites)}", lessons=learned)
    return {"request": task.instruction, "history": history, "memory_hits": hits, "iterations": 0, "builds": 0,
            "reports": {}, "running": [], "effort": task.effort, "xstate": xs,
            "user_effort": task.effort >= 0}


def _sync_todo(state: MainState, plan: dict, reports: dict | None = None) -> dict:
    """Live TODO = plan steps + real outcomes. Completed work keeps its result and is never regenerated."""
    xs = dict(state.get("xstate") or {})
    reports = reports if reports is not None else (state.get("reports") or {})
    old = {t["id"]: t for t in xs.get("todo", [])}
    todo = []
    for s in plan.get("steps", []):
        t = dict(old.get(s["id"], {"id": s["id"], "attempts": 0}))
        t.update(title=s.get("instruction", "")[:120], agent_id=s["agent_id"])
        r = reports.get(s["id"])
        if r:
            # done = verified success; unverified = attempted but the agent could not prove it; failed = failed
            t["status"] = {"success": "done", "failed": "failed"}.get(r.get("status"), "unverified")
            t["verified"] = r.get("status") == "success"
            t["evidence"] = (r.get("summary") or "")[:160]
            t["error"] = "; ".join(r.get("errors") or [])[:160]
        else:
            t.setdefault("status", "pending")
        todo.append(t)
    xs["todo"] = todo
    xs["success_criteria"] = plan.get("success_criteria") or xs.get("success_criteria", [])
    return xs


def P(state: MainState) -> dict:
    """Budget profile for the current request's seriousness tier."""
    return tiers.profile(state.get("effort", 2) if (state.get("effort") or 0) >= 0 else 2)


def _history_text(state: MainState, n: int | None = None) -> str:
    h = state.get("history") or []
    n = min(n or 99, P(state)["history"])
    return "\n".join(f"{m['role']}: {m['content'][:400]}" for m in h[-n:]) if n else ""


_CRITICAL_RE = re.compile(r"(?i)\b(pay|payment|purchase|buy|money|bank|invoice|security|vulnerab|password|"
                          r"credential|production|prod\b|deploy|delete|irreversible|legal|medical|tax)")


def classify_intent(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("classify_intent", "Understanding the request")
    req = state["request"]

    forced = state.get("effort", -1)
    from .. import routing
    cap = routing.parse_user_cap(req)
    if cap:   # the user's own token limit for THIS request: always obeyed, never "use more?"
        routing.set_user_cap(ctx.run.run_id, cap)
        ctx.emit(f"Token limit: {cap:,} (set by you — obeyed for this request)", user_cap=cap)
        if cap < 400:   # too small for planning + agents: answer directly within the limit
            level = forced if forced >= 0 else 0
            ctx.emit(f"Intent: chat · answering directly within {cap} tokens", intent="chat", effort=level)
            return {"intent": "chat", "effort": level}

    # chat commands: /soul <id>, /souls — no LLM call
    if re.match(r"^\s*/souls?\b", req, re.I):
        text = _soul_command(ctx, req)
        ctx.emit("Soul command", intent="command", effort=0)
        return {"intent": "command", "final": text, "effort": 0}

    # /slash modes: /modes lists them; `/deep-dive /fact-check <request>` sets how this request is worked on
    from .. import modes as _modes
    m = re.match(r"^\s*/modes?\b\s*(.*)$", req, re.I | re.S)
    if m:
        ctx.emit("Modes list", intent="command", effort=0)
        return {"intent": "command", "final": _modes.listing(m.group(1)), "effort": 0}
    chosen, rest, unknown = _modes.parse(req)
    if unknown and not chosen:
        tips = ", ".join(f"`/{s}`" for s in _modes.suggest(unknown[0])) or "none close"
        return {"intent": "command", "effort": 0,
                "final": f"I don't know the mode `/{unknown[0]}`. Did you mean: {tips}? Type `/modes` to see all."}
    if chosen:
        ctx.run.directives = _modes.directive_block(chosen)
        level = forced if forced >= 0 else max(x["effort"] for x in chosen)
        routing._run_tier[ctx.run.run_id] = level
        kinds = {x["kind"] for x in chosen}
        intent = "schedule" if "schedule" in kinds else ("task" if kinds & {"task", "ask"} else "chat")
        ctx.emit("Modes: " + ", ".join(x["name"] for x in chosen) + f" · {intent} · seriousness {tiers.NAMES[level]}",
                 intent=intent, effort=level, modes=[x["id"] for x in chosen])
        return {"intent": intent, "effort": level, "modes": [x["id"] for x in chosen], "force_ask": "ask" in kinds,
                "request": rest or req}

    def fallback():
        lvl = tiers.heuristic(req)
        if BUILD_RE.search(req):   # "create an agent that ... weekly" is a build, not a schedule
            return {"intent": "build_agent", "seriousness": max(lvl, 3)}
        if SCHEDULE_RE.search(req):
            return {"intent": "schedule", "seriousness": 1}
        if re.search(r"\b(list|show|disable|enable|delete|remove)\b.{0,20}\bagents?\b", req, re.I):
            return {"intent": "manage", "seriousness": 0}
        if any(re.search(p, req, re.I) for p, _ in CAP_HINTS) or len(req) > 160:
            return {"intent": "task", "seriousness": lvl}
        return {"intent": "chat", "seriousness": 0}

    # trivially short greetings never need an LLM round-trip to classify
    if tiers.heuristic(req) == 0 and not BUILD_RE.search(req):
        out = {"intent": "chat", "seriousness": 0, "reason": "greeting"}
        level = forced if forced >= 0 else 0
        ctx.emit(f"Intent: chat · seriousness {tiers.NAMES[level]}", intent="chat", effort=level)
        return {"intent": "chat", "effort": level}

    out = ctx.llm().json(
        "You are the intent classifier of a multi-agent platform. Intents:\n"
        "chat = small talk / a simple question you can answer directly without tools;\n"
        "task = work that specialised agents should do (coding, research, analysis, writing, browsing, ...);\n"
        "build_agent = the user explicitly wants a NEW reusable agent created;\n"
        "schedule = the user wants something done on a recurring schedule / reminder;\n"
        "manage = list/enable/disable/delete agents;\n"
        "clarify = the request is so ambiguous that doing work now would likely waste effort (missing the key "
        "input, e.g. 'analyze my file' with no file). Use rarely; prefer task with stated assumptions.\n"
        "Also rate SERIOUSNESS (how much care/reasoning the work deserves; it sets the token budget):\n"
        "Rate by the work ACTUALLY required, not by how big the topic sounds; short or vague requests are <=2.\n"
        "0 trivial (greeting, one-line fact) · 1 light (short answer/edit) · 2 standard (normal multi-step task) · "
        "3 deep (multi-file code, research report, building an agent) · 4 critical (money, security, production, "
        "irreversible or external side effects).\n"
        'Return {"intent": "...", "seriousness": 0-4, "reason": "short", "question": "only for clarify"}',
        f"Conversation so far:\n{_history_text(state, 4)}\n\nNew message:\n{req}", fallback,
        validate=lambda d: d.get("intent") in ("chat", "task", "build_agent", "schedule", "manage", "clarify")
        and (d["intent"] != "clarify" or bool(d.get("question"))), role="worker", max_tokens=200)
    from .. import routing
    if ctx.llm().mode != "mock" and routing.models_down(ctx.run.run_id):
        # every model failed (provider outage / rate limits): say so — never guess an intent and answer with a
        # canned greeting marked "success" (seen: "buy the cheapest laptop" → "Hi! I'm AgentOS…")
        why = (ctx.llm().last_error or "no model answered")[:200]
        ctx.emit(f"AI models unavailable — stopping: {why}", status="failed")
        return {"intent": "command", "effort": 0, "llm_down": True,
                "final": "⚠️ **I couldn't work on this: none of my AI models are answering right now** (the model "
                         f"gateway returned errors / rate limits: `{why}`). Nothing was done. Please try again in a "
                         "few minutes."}
    if out.get("intent") == "chat" and _names_site(req) and _ACT_ON_SITE_RE.search(req):
        # doing something ON a website is work, never small talk (T4: "play songs on youtube" was answered in
        # chat with a pretend "let me delegate this to the Browser Agent" and a made-up playlist)
        out = {**out, "intent": "task", "seriousness": max(1, int(out.get("seriousness") or 1))}
    level = forced if forced >= 0 else tiers.clamp(out.get("seriousness", tiers.heuristic(req)))
    if forced < 0 and level == 4 and not _CRITICAL_RE.search(req):
        level = 3   # "critical" (slowest, priciest models) only for money/security/production/irreversible work
    if forced < 0 and (out["intent"] == "build_agent" or _direct_route(req) in ("tool_builder_agent",
                                                                                "skill_writer_agent")):
        level = max(level, 3)            # building agents/tools/skills is deep work (code + tests + verification)
    from .. import routing
    routing._run_tier[ctx.run.run_id] = level   # the request's token budget follows the classified tier
    ctx.emit(f"Intent: {out['intent']} · seriousness {tiers.NAMES[level]}", intent=out["intent"],
             reason=out.get("reason", ""), effort=level, budget=tiers.profile(level))
    if out["intent"] == "clarify":   # NEVER ask in text: the question becomes a card, then the task continues
        return {"intent": "task", "effort": level, "seed_question": out.get("question", "")}
    return {"intent": out["intent"], "effort": level}


def _soul_command(ctx, req: str) -> str:
    """`/souls` lists souls; `/soul <id>` switches this conversation's soul; `/soul` shows the active one."""
    from .. import souls
    cid = ctx.run.conversation_id
    parts = req.strip().split(maxsplit=1)
    all_souls = souls.list_souls()
    active = souls.resolve(cid)
    if parts[0].lower() == "/souls" or (len(parts) == 1 and parts[0].lower() == "/soul" and not cid):
        rows = [f"| {s['icon']} | `{s['id']}` | {s['name']} | {s['description'][:80]} |"
                + (" ← active" if s["id"] == active else "") for s in all_souls]
        return ("| | id | Soul | Description |\n|---|---|---|---|\n" + "\n".join(rows)
                + "\n\nSwitch with `/soul <id>`.")
    if len(parts) == 1:
        s = souls.get_soul(active) or {}
        return f"Active soul: {s.get('icon', '')} **{s.get('name', active)}** (`{active}`). List all with `/souls`."
    want = parts[1].strip().lower()
    match = next((s for s in all_souls if want in (s["id"], s["name"].lower())), None)
    if not match:
        return f"No soul named `{want}`. Available: " + ", ".join(f"`{s['id']}`" for s in all_souls)
    if cid:
        registry.set_conversation_soul(cid, match["id"])
    else:
        from .. import settings
        settings.update({"default_soul": match["id"]})
    return f"{match['icon']} Switched to **{match['name']}** — {match['description']}"


def _persona(ctx, state: MainState) -> str:
    """Soul + identity + user block for user-facing text, sized by the seriousness tier."""
    from .. import souls
    try:
        return souls.system_prefix(souls.resolve(ctx.run.conversation_id), P(state)["level"]) + "\n\n"
    except Exception:  # noqa: BLE001 - persona must never break a run
        return ""


def _modes_of(state) -> list[dict]:
    from .. import modes as _modes
    return [_modes.MODES[i] for i in state.get("modes") or [] if i in _modes.MODES]


def _stream_final(ctx, system: str, user: str, fallback, role: str = "main", max_tokens: int | None = None) -> str:
    """Stream the answer to the UI as `token` events (ChatGPT-style typing).
    A token limit the user gave is obeyed: the model is told the limit and the reply is capped to what is left."""
    from .. import routing
    if getattr(ctx.run, "directives", ""):   # /slash modes shape the answer too
        system += "\n\n" + ctx.run.directives
    steer = ctx.run.steer_block() if getattr(ctx.run, "steer", None) else ""
    if steer:   # messages the user sent while the task ran: the answer must reflect them
        system += "\n\n" + steer
    cap = routing.user_cap(ctx.run.run_id)
    if cap:
        left = routing.cap_left(ctx.run.run_id) or 0
        system += (f"\n\nTOKEN LIMIT SET BY THE USER: your whole reply must fit in {left} tokens "
                   f"(about {max(1, int(left * 0.7))} words). Be as useful as possible within it.")
    text = ctx.llm().stream(system, user, lambda t: ctx.run.emit("token", "main_agent", "running", "", text=t),
                            fallback, role=role, max_tokens=max_tokens)
    if cap:
        used = routing.run_out_tokens(ctx.run.run_id)
        note = (f"\n\n_Stopped at your {cap:,}-token limit (used {used:,})._" if used >= cap
                else f"\n\n_Used {used:,} of your {cap:,}-token limit._")
        ctx.run.emit("token", "main_agent", "running", "", text=note)
        text += note
    return text


def respond(state: MainState, config) -> dict:
    from .. import souls
    ctx = get_ctx(config)
    ctx.node("respond", "Answering directly")
    mem = "\n".join(f"- {m['text']}" for m in state.get("memory_hits") or [])
    agents = ", ".join(a.name for a in registry.list_agents(False)[:40])
    name = souls.assistant_name()
    text = _stream_final(
        ctx, _persona(ctx, state) + f"You are {name}, the main agent of a personal AI agent platform. You manage a "
        f"team of specialised agents ({agents}). Answer helpfully in markdown. Anything you can do directly in text (brainstorm names/ideas, explain, "
        "short writing, advice, rewriting) you DO right now in this answer, completely; never describe what you "
        "'will' do or offer to delegate it. You CANNOT act from here (no browser, files or apps): never claim you "
        "are opening, playing, delegating or submitting anything. Do NOT ask the user questions in your text (questions "
        "are asked only through separate question cards).\nRelevant memories:\n" + (mem or "(none)"),
        f"{_history_text(state)}\nuser: {state['request']}",
        lambda: (f"Hi! I'm {name}. I can delegate work to "
                 f"{len(registry.list_agents(False))} specialised agents — coding, research, data analysis, writing, "
                 "browser automation and more — and build new agents when needed. What should we do?"),
        role=P(state)["model_role"], max_tokens=P(state)["max_tokens"])
    return {"final": text}


def schedule_task(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("schedule_task", "Creating a scheduled task")
    from ..scheduler import jobs
    req = state["request"]

    def fallback():
        low = req.lower()
        days = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 0}
        m = re.search(r"(\d{1,2})(?::(\d\d))?\s*(am|pm)?", low)
        hour, minute = 8, 0
        if m:
            hour, minute = int(m.group(1)) % 24, int(m.group(2) or 0)
            if m.group(3) == "pm" and hour < 12:
                hour += 12
        dow = next((str(v) for k, v in days.items() if k in low), "*")
        cron = f"0 * * * *" if "hour" in low else f"{minute} {hour} * * {dow if 'week' in low or dow != '*' else '*'}"
        task = re.sub(r"(?i)\b(every|each)\s+\w+(\s+at\s+[\d:apm ]+)?|\bdaily\b|\bweekly\b|remind me to", "", req)
        return {"name": task.strip()[:50] or "Scheduled task", "cron": cron, "prompt": task.strip() or req}

    def valid(d):
        try:
            jobs.parse(d["cron"])
            return bool(d.get("prompt"))
        except Exception:  # noqa: BLE001
            return False

    d = ctx.llm().json(
        "Convert the user's recurring request into a schedule. Return {\"name\": short title, \"cron\": 5-field "
        "cron in local time (min hour dom month dow, 0=Sunday), \"prompt\": the instruction to run each time}.",
        req, fallback, validate=valid)
    s = jobs.create(d["name"], d["cron"], d["prompt"], channel="default")
    return {"final": f"⏰ Scheduled **{s['name']}** (`{s['cron']}`). Each run's result will appear on your Home "
                     f"feed and channels.\n\nPrompt: _{s['prompt']}_"}


def manage_agents(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("manage_agents", "Managing the agent registry")
    agents = registry.list_agents()
    req = state["request"]

    def fallback():
        low = req.lower()
        act = next((a for a in ("disable", "enable", "delete", "remove") if a in low), "list")
        target = next((a.id for a in agents if a.name.lower() in low or a.id in low), "")
        return {"action": "delete" if act == "remove" else act, "agent_id": target}

    d = ctx.llm().json(
        'Decide the registry action. Return {"action": "list"|"enable"|"disable"|"delete", "agent_id": "..."}.\n'
        "Agents: " + json.dumps([{"id": a.id, "name": a.name} for a in agents]), req, fallback)
    act, aid = d.get("action", "list"), d.get("agent_id", "")
    spec = registry.get_agent(aid) if aid else None
    if act == "list" or not spec:
        lines = [f"| {a.icon} {a.name} | {a.type} | {a.status} | {a.description[:70]} |" for a in agents]
        return {"final": "| Agent | Type | Status | Description |\n|---|---|---|---|\n" + "\n".join(lines)}
    if spec.type in ("system",) or (act == "delete" and spec.type == "prebuilt"):
        return {"final": f"{spec.name} is a {spec.type} agent and can't be {act}d. You can disable prebuilt agents."}
    if act == "delete":
        registry.delete_agent(spec.id)
        return {"final": f"Deleted **{spec.name}**."}
    spec.status = "active" if act == "enable" else "disabled"
    ctx.runtime.register(spec)
    return {"final": f"**{spec.name}** is now {spec.status}."}


def analyze_capabilities(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("analyze_capabilities", "Working out which capabilities are needed")
    req = state["request"]

    def fallback():
        caps = [c for p, c in CAP_HINTS if re.search(p, req, re.I)]
        return {"capabilities": list(dict.fromkeys(caps)) or ["general"],
                "success_criteria": ["The user's request is fully addressed"]}

    d = ctx.llm().json(
        "List the capabilities needed to fulfil the request (short snake_case like data_analysis, web_research, "
        "coding, code_review, testing, browser_automation, writing, planning...) and concrete success criteria. "
        'Return {"capabilities": [...], "success_criteria": [...]}',
        f"{_history_text(state, 4)}\nRequest: {req}", fallback,
        validate=lambda x: isinstance(x.get("capabilities"), list))
    ctx.emit("Capabilities: " + ", ".join(d["capabilities"]), capabilities=d["capabilities"])
    return {"caps": d["capabilities"], "criteria": d.get("success_criteria", [])}


_OPEN_ENDED = re.compile(r"(?i)\b(search|research|find|look up|report|compare|analy[sz]e|investigate|explore|"
                         r"recommend|plan|write|build|create|design|detailed|everything about)\b")


_ASK_ME = re.compile(r"(?i)\b(ask me|question me|quiz me|clarify with me|ask (me )?(some |a few |any )?questions?|"
                     r"before you (start|begin)|check with me)\b")


def ask_user(state: MainState, config) -> dict:
    """UNDERSTAND before acting: for searches and open-ended work, ask the user up to 3 short questions (what exactly
    to search, depth, focus, format). Skipped for clear/simple requests. Unanswered → sensible defaults, stated."""
    ctx = get_ctx(config)
    req = state["request"]
    seed = state.get("seed_question") or ""  # the classifier found the request unclear → ask (as a CARD, never text)
    forced = bool(_ASK_ME.search(req)) or bool(seed) or bool(state.get("force_ask"))
    if state.get("questions_asked") or ctx.llm().mode == "mock" or (not forced and (
            ctx.run.kind not in ("chat", "agent") or P(state)["level"] < 1 or not _OPEN_ENDED.search(req))):
        return {}
    ctx.node("ask_user", "Checking what you want exactly")
    d = ctx.llm().json(
        ("Write the 1-4 most useful questions to ask the user before doing this (they asked to be asked / the "
         "request is unclear). Use the conversation for context; don't ask what is already known. "
         if forced else
         "Decide if the request is ambiguous enough that 1-3 short clarifying questions would clearly improve the "
         "result (e.g. what exactly to search for, which aspects, depth, sources, output format, audience). If it "
         "is already specific, return no questions. ")
        + "Each question needs 2-4 short suggested options and a default. Every option and the default must be a "
          "CONCRETE choice the agents can act on without asking again (e.g. 'Two Sum (#1, easy)', 'Python'), never "
          "a placeholder like 'Specify the problem' or 'Other'; the default is your best pick for the user. "
          "Ask ONLY about WHAT the user wants (content, choice, taste, scope) — NEVER about HOW to do it: no "
          "questions about tools, agents, browsers, browser/computer control, profiles, windows, tabs, files or "
          "settings; those are your decisions. "
        'Return {"questions": [{"q": str, "options": [str], "default": str}]}',
        f"Request: {req}\n" + (f"Main open question: {seed}\n" if seed else "")
        + f"Conversation:\n{_history_text(state, 6)}",
        lambda: {"questions": [{"q": seed, "options": [], "default": ""}] if seed else []},
        validate=lambda x: isinstance(x.get("questions"), list), role="worker", max_tokens=600)
    qs = [q for q in d["questions"] if isinstance(q, dict) and q.get("q")][:4]
    if not qs:
        return {"questions_asked": True}
    from .. import settings
    from ..core import questions
    answers = questions.ask(ctx.run, "main_agent", qs, timeout=120 if settings.get("auto_mode") else 600)
    clar = "\n".join(f"- {q['q']} → {a}" for q, a in zip(qs, answers) if a)
    xs = dict(state.get("xstate") or {})
    if clar:
        xs["goal"] = req + "\nUser clarifications:\n" + clar
    note = ("\n\nUser clarifications (already answered — now DO the task with them; do NOT write more questions "
            "for the user):\n" + clar) if clar else ""
    return {"questions_asked": True, "request": req + note, "xstate": xs}


def discover_agents(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("discover_agents", "Searching the agent registry")
    query = state["request"] + " " + " ".join(state.get("caps") or [])
    scored = [(s, a) for s, a in registry.search(query, limit=40) if a.id not in SYSTEM_IDS]
    cands = [dict(_agent_brief(a), score=s) for s, a in scored]
    have = {c["id"] for c in cands}
    for mm in _modes_of(state):          # agents preferred by the user's /modes are always offered to the planner
        a = registry.get_agent(mm["agent"]) if mm["agent"] and mm["agent"] not in have else None
        if a and a.status == "active":
            cands.insert(0, dict(_agent_brief(a), score=99))
            have.add(a.id)
    ctx.emit(f"Found {sum(1 for c in cands if c['score'] > 0)} relevant agents",
             candidates=[{"id": c["id"], "score": c["score"]} for c in cands[:10]])
    return {"candidates": cands}


_SHOT_RE = re.compile(r"(?i)\b(screenshot|screen ?shot|screen capture|capture)\b")
_SCREEN_RE = re.compile(r"(?i)\b(my|the|whole|entire) (screen|desktop|computer|monitor)\b|\bwhat'?s on my screen\b")
_TERMINAL_RE = re.compile(r"(?i)`[^`]+`|^\s*(please\s+)?(run|execute)\b|\b(terminal|command line|shell|powershell|"
                          r"bash)\b|\b(list|show) (the |all |my )?files\b|\bpip install\b|\bnpm install\b|\bgit status\b|"
                          r"\b(and|then)\s+run (it|this|that)\b")
_ACT_ON_SITE_RE = re.compile(r"(?i)\b(play|watch|listen|open|go to|visit|search|submit|buy|order|book|download|"
                             r"upload|post|send|log ?in|sign ?in|navigate|click|fill|subscribe|solve)\b")


def _names_site(req: str) -> bool:
    """A URL/domain, 'this page', or a well-known site by name ('youtube', 'leetcode', 'github'…)."""
    from .. import site_aliases   # seeded + LEARNED from every site the agents have visited
    return bool(_URL_OR_SITE_RE.search(req)) or bool(site_aliases.names_in(req))


_URL_OR_SITE_RE = re.compile(r"(?i)https?://|\b[\w-]+\.(com|org|net|io|dev|ai|in)\b|\b(this|the) (web ?page|page|site|website)\b")


def _direct_route(req: str) -> str | None:
    """Deterministic routes for computer-use requests (screen screenshots, browser pages, terminal commands)."""
    if len(req) > 400:
        return None
    active = {a.id for a in registry.list_agents(False)}
    if re.search(r"(?i)\b(create|make|build|write|add|give (it|yourself))\b.{0,30}\b(new |a |custom )?tools?\b", req) \
            and not re.search(r"(?i)\bagent\b", req) and "tool_builder_agent" in active:
        return "tool_builder_agent"
    if re.search(r"(?i)\b(create|make|write|add|save)\b.{0,30}\b(a |new )?skills?\b", req) \
            and "skill_writer_agent" in active:
        return "skill_writer_agent"
    if _SHOT_RE.search(req) and _SCREEN_RE.search(req) and not _URL_OR_SITE_RE.search(req) \
            and "desktop_agent" in active:
        return "desktop_agent"
    # the user's REAL Chrome profile + work that needs their login (solve/submit/post...) → let the planner build
    # solver → Computer Agent steps (browser_agent can't drive real profiles)
    try:
        from ..core.execstate import parse_profile
        from ..tools.browser import _is_real_profile
        prof = parse_profile(req)
        if prof and _is_real_profile(prof) and re.search(
                r"(?i)\b(submit|solve|post|send|log ?in|logged|my account|accepted|apply|buy|comment|reply)\b", req):
            return None
    except Exception:  # noqa: BLE001
        pass
    # tabs are browser actions, not separate work items: "open 5 tabs" = ONE browser agent
    pure_tabs = re.search(r"(?i)\b(open|close|switch)\b.{0,40}\btabs?\b", req) and not re.search(
        r"(?i)\b(solve|answer|write|code|summari[sz]e|analy[sz]e|compare)\b", req)
    if (pure_tabs or (_names_site(req) and re.search(
            r"(?i)\b(chrome|browser|open|go to|visit|screenshot|navigate|play|watch|listen|stream)\b", req))) \
            and "browser_agent" in active:
        return "browser_agent"
    if _TERMINAL_RE.search(req) and "terminal_agent" in active and not re.search(
            r"(?i)\b(refactor|implement|feature|bug|fix the|website|app|application|architecture)\b", req):
        return "terminal_agent"
    return None


_NUM_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}


def _batch_size(req: str) -> int:
    """'open ten leetcode questions' -> 10 (0 when the request isn't a batch of separate items)."""
    m = re.search(r"\b(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s+(?:\w+\s+){0,3}?(questions?|problems?|tasks?|items?|"
                  r"files?|emails?|articles?|repos?|issues?|exercises?|topics?|essays?|reports?)\b", req, re.I)
    if not m:
        return 0
    v = m.group(1).lower()
    n = int(v) if v.isdigit() else _NUM_WORDS[v]
    return n if 2 <= n <= 30 else 0


# typical tokens one run of an agent kind uses (refined over time by real usage — see _learned_cost)
_COST = {"coding": 25000, "builder": 30000, "research": 14000, "browser": 9000, "desktop": 9000, "data": 8000,
         "writer": 7000, "composite": 25000, "debug": 15000, "testing": 12000, "review": 9000, "competitive": 9000,
         "toolsmith": 8000, "skillsmith": 3000, "terminal": 5000, "generic": 6000}


def _est(agent_id: str) -> int:
    spec = registry.get_agent(agent_id)
    graph = spec.graph if spec else "generic"
    return _learned_cost(agent_id) or _COST.get(graph, 6000)


def _learned_cost(agent_id: str) -> int:
    """Average tokens this agent really used in its last runs (reports carry `tokens`)."""
    reps = [r for r in registry.reports_for_agent(agent_id, 10) if r.get("tokens")]
    return int(sum(r["tokens"] for r in reps) / len(reps)) if len(reps) >= 3 else 0


def _allocate_budget(ctx, state: MainState, steps: list[dict]) -> list[dict]:
    """ESTIMATE the plan's cost before running it. If it exceeds the request's budget, spread the budget EVENLY:
    every step still runs, but over-budget plans run their steps at a lower effort (cheaper model, shorter output)
    instead of skipping steps later."""
    from .. import routing
    if not steps:
        return steps
    level = P(state)["level"]
    budget = routing.budget_for(ctx.run.run_id, level)
    for s in steps:   # generous: 1.5x what this agent normally uses, so plans have room to spare
        s["est_tokens"] = int((int(s.get("est_tokens") or 0) or _est(s["agent_id"])) * 1.5)
    total = sum(s["est_tokens"] for s in steps)
    lighter = False
    cap = routing.user_cap(ctx.run.run_id)
    if cap and total > cap:   # the USER's limit: fit the plan into it (never ask, never exceed)
        for s in steps:
            s["effort"] = max(0, level - 2)
            s["est_tokens"] = max(1, int(s["est_tokens"] * cap / total))
        ctx.emit(f"Plan fitted to your {cap:,}-token limit (cheaper/shorter steps)", estimate=cap)
        return steps
    if cap:
        pass
    elif total > budget and not state.get("user_effort"):   # the user's chosen effort is never changed by the AI
        # ASK for more tokens instead of silently cutting quality (default when unanswered: use more = full quality)
        from ..core import questions
        ans = questions.ask(ctx.run, "main_agent", [{
            "q": f"This task needs about {total:,} tokens (normal budget for this request is {budget:,}). "
                 f"Use more tokens for full quality, or keep it lighter?",
            "options": ["Use more tokens (full quality)", "Keep it lighter"],
            "default": "Use more tokens (full quality)"}], timeout=90)
        lighter = bool(ans and "lighter" in ans[0].lower())
        if lighter:
            for s in steps:
                s["effort"] = max(0, level - 1)
                s["est_tokens"] = int(s["est_tokens"] * budget / total)
        else:
            routing._run_budget[ctx.run.run_id] = max(budget, int(total * 1.2))   # raise this request's budget
    est = sum(s["est_tokens"] for s in steps)
    ctx.emit(f"Estimate: ~{est:,} tokens for {len(steps)} step(s)" + (" (lighter, as you chose)" if lighter else ""),
             estimate=est, budget=routing.budget_for(ctx.run.run_id, level))
    return steps


def plan_dag(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("plan_dag", "Building the execution plan")
    req, cands = state["request"], state.get("candidates") or []
    ids = {c["id"] for c in cands}
    wants_build = state.get("intent") == "build_agent" and not state.get("builds")

    direct = _direct_route(req)
    if wants_build and direct not in ("tool_builder_agent", "skill_writer_agent"):
        direct = None
    if direct:   # unambiguous computer-use requests: skip the planner LLM call (fast + zero tokens)
        plan = Plan(intent=req[:120], required_capabilities=state.get("caps") or [],
                    steps=[PlanStep(id="s1", agent_id=direct, instruction=req)]).model_dump()
        _plan_event(ctx, plan)
        return {"plan": plan}

    def fallback():
        from ..registry import tokens
        caps = state.get("caps") or []
        if wants_build:
            best = cands[0] if cands else None
            strong = best and len(tokens(req) & tokens(best["name"] + " " + best["description"] + " "
                                                       + " ".join(best["capabilities"]))) >= 4
            if not strong:
                name = re.sub(r"(?i).*?\bagent (that|which|to|for)\b", "", req).strip(" .") or req
                return {"intent": req[:120], "required_capabilities": caps, "steps": [],
                        "missing": {"name": name[:80], "description": req, "capabilities": caps}}
        steps, used = [], set()
        for cap in caps:
            match = next((c for c in cands if cap in c["capabilities"] and c["id"] not in used), None)
            if match:
                used.add(match["id"])
                steps.append({"id": f"s{len(steps) + 1}", "agent_id": match["id"], "instruction": req,
                              "depends_on": [], "expected_output": cap})
        if not steps and cands and cands[0]["score"] > 0:
            steps.append({"id": "s1", "agent_id": cands[0]["id"], "instruction": req, "depends_on": []})
        if len(steps) > 1:  # later steps build on earlier ones; final writer-ish step depends on all
            for s in steps[1:]:
                s["depends_on"] = [steps[0]["id"]]
        return {"intent": req[:120], "required_capabilities": caps, "steps": steps[:4], "missing": None,
                "direct_answer": None if steps else "No suitable agent found."}

    def valid(d):
        steps = d.get("steps") or []
        return all(s.get("agent_id") in ids or (s.get("agent_id") == "spawn" and s.get("role")) for s in steps)             and (steps or d.get("missing") or d.get("direct_answer"))

    built = f"\nAn agent was just built for this: {state.get('build_failed') or ''}" if state.get("builds") else ""
    d = ctx.llm().json(
        "You are the planner of a multi-agent platform. Given the request and the available agents, produce a DAG "
        "plan. Use ONLY agent ids from the list. Give each step a precise instruction for that agent (include all "
        "data/context the agent needs from the request). PARALLEL FIRST: steps without depends_on run AT THE SAME "
        "TIME on different agents. Split work into independent parallel parts whenever possible; use depends_on ONLY "
        "when a step truly cannot start without another step's output (never chain plan->code->test->docs by habit: "
        "e.g. a design step and a code step can run together, tests and docs can run in parallel). Use the fewest "
        "agents that do the job well, and never use a team-lead agent (software_dev_agent, research_agent) together "
        "with its own member agents. SPAWN: when no listed agent fits a sub-task well, use agent_id \"spawn\" and add "
        "\"role\": a one-paragraph system prompt for a temporary specialist (e.g. 'You are a security auditor...'); "
        "spawned workers are created on the fly, run in parallel, can search/read the web, memory and files, and are "
        "discarded afterwards. If NO existing agent can do a core part "
        "of the request (or the user explicitly asked for a new reusable agent and none matches), set `missing` to "
        "describe the agent to build and leave steps empty. If the request needs no agents, set direct_answer.\n"
        "REAL CHROME PROFILE: when the user names THEIR Chrome profile (e.g. 'profile asdf') and the step needs their "
        "login (submit, post, send, my account, logged in), use desktop_agent (Computer Agent, works in real profiles "
        "via the screen) — NOT browser_agent (browser_agent only has AgentOS's own empty profiles). Give the "
        "desktop_agent step the URL and depend on the step that produces any code/text it must paste. Name a Chrome "
        "profile in a step ONLY if the user named it in THIS request — never take one from memories, experience "
        "notes or earlier tasks.\n"
        + _vm_rule() +
        "WORK ALONGSIDE THE USER: the user keeps using the laptop while you work. For web tasks (open a site, search, "
        "play a video/song, read pages) use browser_agent — its own visible browser window needs NO mouse/keyboard, "
        "so both work at the same time (sound plays on the laptop). Use desktop_agent (takes over mouse/keyboard) "
        "ONLY when the user's own logged-in Chrome profile or a desktop app is really needed. Use the FEWEST steps: "
        "e.g. 'play a song on YouTube' is ONE browser_agent step.\n"
        "CHEAPEST FIT: pick the most specific, cheapest agent for each step. Algorithm / LeetCode / Codeforces / CSES "
        "problems → competitive_programming_agent (NOT coding_agent). coding_agent / language agents only change a "
        "real code project (files in /workspace or a repo). A step that only needs text → writer/summarizer.\n"
        "DELIVERABLE FILES: anything the user will open or run (a website, HTML page, app, script, game, document "
        "file) → coding_agent (or a language agent), which WRITES the real file and checks it; never a spawned "
        "worker (spawned workers cannot write files). Put copy/design requirements in that step's instruction "
        "instead of a separate writing step unless the text itself is substantial.\n"
        "BATCH REQUESTS: if the user asks for N separate items (e.g. '10 problems', '5 emails', 'each file'), create "
        "ONE step PER ITEM (or per group of <=3 items) so they run in parallel — never give one agent all N. Name the "
        "concrete item in each instruction (e.g. 'LeetCode #3 Longest Substring...'). For 'solve on a website': first "
        "a browser step that collects the item list/URLs, then per-item steps that depend on it (solve, then a "
        "browser step that opens that item in a NEW TAB and puts the code in the editor with set_code).\n"
        'Return {"intent": str, "required_capabilities": [..], "success_criteria": [..], "steps": [{"id": "s1", '
        '"agent_id": str, "instruction": str, "depends_on": [], "expected_output": str}], '
        '"missing": null | {"name": str, "description": str, "capabilities": [..], "suggested_uses": [agent ids]}, '
        '"direct_answer": null | str}',
        f"Request: {req}\nIntent: {state.get('intent')}\nCapabilities needed: {state.get('caps')}\n"
        + (f"{ctx.run.directives}\nPreferred agents for these modes (use them when they fit): "
           f"{[_m['agent'] for _m in (_modes_of(state)) if _m['agent']]}\n" if state.get('modes') else "") +
        f"Success criteria: {state.get('criteria')}{built}\n"
        f"EXPERIENCE from similar past tasks (follow DO, avoid DON'T): "
        f"{'; '.join((state.get('xstate') or {}).get('lessons', [])[-4:]) or 'none'}\n"
        f"Conversation:\n{_history_text(state, 4)}\n\n"
        f"Available agents:\n{json.dumps([{k: c[k] for k in ('id', 'name', 'description', 'capabilities')} for c in cands], indent=0)}",
        fallback, validate=valid, role=P(state)["model_role"], max_tokens=P(state)["max_tokens"] + 600)
    if state.get("builds") and d.get("missing"):
        d["missing"] = None  # never loop building
    elif wants_build and not d.get("missing") and BUILD_RE.search(req) \
            and not any(s.get("agent_id") == "agent_builder" for s in d.get("steps") or []):
        # the user explicitly asked for a NEW reusable agent ("I need an agent that tracks…"): build it, then the
        # re-plan uses it — 16.23 got a one-off answer from existing agents and no agent was ever made
        from ..registry import tokens
        best = cands[0] if cands else None
        if not (best and len(tokens(req) & tokens(best["name"] + " " + best["description"] + " "
                                                 + " ".join(best["capabilities"]))) >= 4):
            name = re.sub(r"(?i).*?\bagent (that|which|to|for)\b", "", req).strip(" .") or req
            d.update(steps=[], missing={"name": name[:80], "description": req, "capabilities": state.get("caps") or []})
            ctx.emit("You asked for a new agent — building it first, then using it for this request")
    n_items = _batch_size(req)
    if n_items:   # a batch of N items gets a token budget per item (else the last items never run)
        from .. import routing
        routing.set_batch_budget(ctx.run.run_id, P(state)["level"], n_items)
    cap = max(16, 2 * n_items + 2)   # never drop planned work; effort tiers only change models/output length
    cap = min(cap, 32)
    d["steps"] = (d.get("steps") or [])[:cap]
    d["steps"] = [_prefer_vm_browser(x, req) for x in d["steps"]]
    if not n_items:   # simple tasks: same agent chained step after step → ONE bigger step (no re-start per sub-step)
        before = len(d["steps"])
        d["steps"] = _fit_step_cap(_collapse_chains(d["steps"]), STEP_CAP[P(state)["level"]])
        if len(d["steps"]) < before:
            ctx.emit(f"Plan simplified: {before} → {len(d['steps'])} steps (same agent work merged; "
                     f"seriousness {tiers.NAMES[P(state)['level']]} allows {STEP_CAP[P(state)['level']]})")
    d["steps"] = _allocate_budget(ctx, state, d["steps"])   # estimate → spread the budget evenly (never drop work)
    plan = Plan.model_validate({**d, "steps": [PlanStep.model_validate(s) for s in d.get("steps") or []]}).model_dump()
    if not plan["success_criteria"]:
        plan["success_criteria"] = state.get("criteria") or []
    _plan_event(ctx, plan)
    return {"plan": plan}


def _vm_rule() -> str:
    from .. import vmbrowser
    vp = sorted(vmbrowser.profiles())
    if not vp:
        return ""
    return (f"VM BROWSER: these profiles are logged in inside the test VM's browser: {vp}. For WEB work in one of "
            "them use browser_agent (it runs there with the logins, never touching the user's screen) — NOT "
            "desktop_agent.\n")


_WEB_RE = re.compile(r"(?i)https?://|\.(com|org|io|dev|net)\b|\b(website|site|page|tab|leetcode|codeforces|youtube)\b")
_APP_RE = re.compile(r"(?i)\b(notepad|explorer|desktop app|file manager|vs ?code|terminal|word|excel)\b")


def _prefer_vm_browser(step: dict, request: str = "") -> dict:
    """Hard rule (M7): a Computer-Agent web step for a profile that is logged in inside the VM browser goes to
    browser_agent there instead — same logins, no mouse/keyboard, the owner keeps working. Also when this machine
    has no desktop control at all (the Linux VM): web steps can only work in the browser agent."""
    if step.get("agent_id") != "desktop_agent":
        return step
    from .. import vmbrowser
    from ..clients import service_health
    from ..core.execstate import parse_profile
    instr = step.get("instruction", "")
    # the step may not repeat the profile ("on the page already open from s2, paste…"): use the request's
    prof = parse_profile(instr) or parse_profile(request)
    web = _WEB_RE.search(instr) and not _APP_RE.search(instr)
    if web and prof and vmbrowser.has(prof):
        return {**step, "agent_id": "browser_agent",
                "instruction": instr + f"\n(Use browser profile '{prof}' — it is logged in inside the VM browser.)"}
    if web and not service_health().get("bridge"):
        return {**step, "agent_id": "browser_agent",
                "instruction": instr + (f"\n(Use browser profile '{prof}'.)" if prof else "")}
    return step


STEP_CAP = [2, 2, 4, 6, 8]   # max planned steps per seriousness tier (non-batch requests)


def _fit_step_cap(steps: list[dict], cap: int) -> list[dict]:
    """Over the tier's cap → merge SIBLING steps on the same agent (same inputs) into one step that covers all their
    sub-tasks; nothing is dropped. Seen: 'plan a weekend trip' (standard) planned 6 parallel research agents + 2 more
    (861k-token estimate); one research agent covering several sub-questions does the same job."""
    steps = [dict(s, depends_on=list(s.get("depends_on") or [])) for s in steps]
    while len(steps) > cap:
        groups: dict[tuple, list[dict]] = {}
        for s in steps:
            if s.get("agent_id") != "spawn":
                groups.setdefault((s["agent_id"], tuple(sorted(s["depends_on"]))), []).append(s)
        group = max((g for g in groups.values() if len(g) > 1), key=len, default=None)
        if not group:
            break
        a, b = group[0], group[1]
        a["instruction"] = f"{a.get('instruction', '')}\nALSO: {b.get('instruction', '')}".strip()
        for s in steps:
            s["depends_on"] = list(dict.fromkeys(a["id"] if x == b["id"] else x for x in s["depends_on"]))
        steps.remove(b)
    return steps


def _collapse_chains(steps: list[dict]) -> list[dict]:
    """Merge B into A when B runs on the same agent, depends ONLY on A, and nothing else depends on A: the agent
    keeps its page/session/context instead of restarting per sub-step (fewer, bigger steps; fewer tokens)."""
    steps = [dict(s, depends_on=list(s.get("depends_on") or [])) for s in steps]
    changed = True
    while changed:
        changed = False
        for b in steps:
            if len(b["depends_on"]) != 1 or b.get("agent_id") == "spawn":
                continue
            a = next((s for s in steps if s["id"] == b["depends_on"][0]), None)
            if not a or a.get("agent_id") != b.get("agent_id") or \
                    any(a["id"] in s["depends_on"] for s in steps if s is not b):
                continue
            a["instruction"] = f"{a.get('instruction', '')}\nTHEN: {b.get('instruction', '')}".strip()
            if b.get("expected_output"):
                a["expected_output"] = b["expected_output"]
            for s in steps:   # whatever waited for B now waits for the merged A
                s["depends_on"] = [a["id"] if x == b["id"] else x for x in s["depends_on"]]
            steps.remove(b)
            changed = True
            break
    return steps


def _cycle_free(steps: list[dict]) -> list[dict]:
    """Drop unknown/self deps and break cycles (Kahn's algorithm); returns steps in a valid order."""
    ids = {s["id"] for s in steps}
    for s in steps:
        s["depends_on"] = [d for d in dict.fromkeys(s.get("depends_on") or []) if d in ids and d != s["id"]]
    order, done, remaining = [], set(), list(steps)
    while remaining:
        ready = [s for s in remaining if all(d in done for d in s["depends_on"])]
        if not ready:  # cycle: cut the deps of the first remaining step
            remaining[0]["depends_on"] = [d for d in remaining[0]["depends_on"] if d in done]
            continue
        for s in ready:
            order.append(s)
            done.add(s["id"])
            remaining.remove(s)
    return order


_ALGO_RE = re.compile(r"(?i)\b(leetcode|codeforces|cses|atcoder|hackerrank|competitive|algorithm(ic)? problem|"
                      r"two sum|time complexity|optimal (python|c\+\+|java) solution)\b")
_HEAVY_CODERS = {"coding_agent", "python_agent", "javascript_agent", "cpp_agent", "software_dev_agent"}
_PROJECT_RE = re.compile(r"(?i)(/workspace/\S+|\brepo(sitory)?\b|\bproject\b|\bcodebase\b|\bfix the (bug|tests)\b)")


def _cheapest_fit(step: dict, active: dict) -> dict:
    """Hard rule on top of the planner prompt: standalone algorithm problems never go to the heavyweight
    repo-editing coding agents (they explore/test/debug a project and burn the budget)."""
    if (step.get("agent_id") in _HEAVY_CODERS and _ALGO_RE.search(step.get("instruction", ""))
            and not _PROJECT_RE.search(step.get("instruction", "")) and "competitive_programming_agent" in active):
        return {**step, "agent_id": "competitive_programming_agent"}
    return step


_NON_RETRY = re.compile(r"(?i)blocked|token budget|using the computer|not logged in|loop guard|denied|"
                        r"refusing to act|no real chrome profile|owner_active")


def _non_retryable(rep: dict) -> bool:
    """Failures a retry can't fix right now (user busy, approval denied, budget spent, not logged in…)."""
    text = " ".join([rep.get("summary", "")] + [str(e) for e in rep.get("errors") or []]
                    + [str(rep.get("result", ""))[:600]])
    return bool(_NON_RETRY.search(text))


def _outcome_sig(reps: dict) -> str:
    """What the latest round produced: statuses + errors + summaries of the failed/unverified steps."""
    parts = sorted(f"{r.get('agent_id')}:{r.get('status')}:{' '.join(map(str, r.get('errors') or []))[:120]}:"
                   f"{(r.get('summary') or '')[:120]}" for r in reps.values() if r.get("status") != "success")
    return "|".join(parts)


def _similar(a: str, b: str) -> float:
    """Token-set similarity 0..1 (cheap, no model call)."""
    ta, tb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / max(1, len(ta | tb))


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()[:300]


def _no_repeat_plans(follow: list[dict], steps: list[dict], reps: dict) -> list[dict]:
    """Loop detection for PLANS: a follow-up that repeats an already-verified step is dropped; one that repeats a
    failed step must carry the failure reason and a different strategy (never the identical instruction again)."""
    by_text = {(s["agent_id"], _norm(s["instruction"])): s for s in steps}
    out = []
    for f in follow:
        prev = by_text.get((f.get("agent_id"), _norm(f.get("instruction", ""))))
        if prev:
            r = reps.get(prev["id"]) or {}
            if r.get("status") == "success":
                continue                                    # completed + verified: never regenerate
            why = "; ".join(r.get("errors") or [r.get("summary", "")])[:300]
            f = dict(f, instruction=f["instruction"] + f"\nThe identical attempt already failed ({why}). Use a "
                                                       "DIFFERENT strategy and continue from the current state.")
        out.append(f)
    return out


def refine_plan(state: MainState, config) -> dict:
    """Validate the DAG, then (for multi-step plans) let a critic improve instructions/ordering."""
    ctx = get_ctx(config)
    plan = dict(state["plan"])
    if not plan.get("steps"):
        return {}
    ctx.node("refine_plan", "Validating and critiquing the plan")
    active = {a.id: a for a in registry.list_agents(False)}
    steps = [s for s in plan["steps"] if s["agent_id"] in active or s["agent_id"] == "spawn"]
    seen, uniq = set(), []
    for s in steps:  # identical (agent, instruction) pairs are wasted work
        key = (s["agent_id"], s["instruction"].strip().lower())
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    steps = _cycle_free([_cheapest_fit(s, active) for s in uniq])
    critic = P(state)["critic"]
    # critic costs a full extra LLM call: only for critical work or big plans
    use_critic = P(state)["level"] >= 4 or (critic and len(steps) >= 5)
    if len(steps) >= 2 and use_critic and ctx.llm().mode != "mock":
        brief = [{"id": s["id"], "agent": s["agent_id"], "instruction": s["instruction"],
                  "depends_on": s["depends_on"]} for s in steps]
        d = ctx.llm().json(
            "You are a plan critic. Improve this multi-agent plan: make each instruction self-contained and "
            "specific (agents can't see the user's message unless you include what they need), fix missing "
            "dependencies (a step that consumes another step's output must depend on it), and remove redundant "
            "steps. Keep ids and agents unless clearly wrong. "
            'Return {"steps": [{"id","agent_id","instruction","depends_on","expected_output"}], "notes": str}',
            f"User request: {state['request']}\nPlan: {json.dumps(brief)}",
            lambda: {"steps": steps, "notes": ""},
            validate=lambda x: x.get("steps") and all(s.get("agent_id") in active or s.get("agent_id") == "spawn" for s in x["steps"]),
            max_tokens=P(state)["max_tokens"] + 600)
        steps = _cycle_free([PlanStep.model_validate(s).model_dump() for s in d["steps"]])
        if d.get("notes"):
            ctx.emit("Plan critique: " + str(d["notes"])[:300])
    plan["steps"] = steps
    _plan_event(ctx, plan)
    return {"plan": plan, "xstate": _sync_todo(state, plan)}   # live TODO starts from the plan


def route_after_plan(state: MainState) -> str:
    p = state["plan"]
    if p.get("missing") and not state.get("builds"):
        return "build_agent"
    if p.get("steps"):
        return "risk_gate"
    return "synthesize"


def build_agent(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    miss = state["plan"]["missing"]
    ctx.node("build_agent", f"No suitable agent — asking the Agent Builder to create '{miss['name']}'")
    instr = (f"Build a new reusable agent.\nName idea: {miss['name']}\nPurpose: {miss['description']}\n"
             f"Capabilities: {', '.join(miss.get('capabilities') or [])}\n"
             f"Consider reusing: {', '.join(miss.get('suggested_uses') or [])}\n"
             f"Original user request: {state['request']}")
    rep = ctx.delegate("agent_builder", instr)
    note = "" if rep.status != "failed" else f"Agent Builder failed: {rep.summary}"
    reports = {f"build{state.get('builds', 0) + 1}": rep.model_dump()}
    return {"builds": state.get("builds", 0) + 1, "build_failed": note or rep.summary, "reports": reports}


def risk_gate(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("risk_gate", "Checking permissions of the planned agents")
    from .browser import ACTION_WORDS, SCREENSHOT_ONLY
    risky = []
    for s in state["plan"]["steps"]:
        spec = registry.get_agent(s["agent_id"])
        if not spec or not spec.permissions.desktop:
            # host-Chrome use is approved once per run by the browser tool itself (no double prompt)
            continue
        if SCREENSHOT_ONLY.search(s["instruction"]) and not ACTION_WORDS.search(s["instruction"]):
            continue  # read-only screen capture; every desktop action is still approved individually
        risky.append(f"{spec.name} (desktop control)")
    if risky:
        ctx.approve("Plan uses agents that can act on your real computer: " + ", ".join(risky),
                    {"plan": state["plan"]})
    return {}


def _ready(state: MainState) -> list[dict]:
    done = set((state.get("reports") or {}).keys())
    running = set(state.get("running") or [])
    out = []
    by_id = {s["id"]: s for s in state["plan"]["steps"]}
    # ONE screen, ONE mouse/keyboard: Computer Agent steps never run in parallel with each other
    busy_single = any(by_id.get(r, {}).get("agent_id") in _SINGLE_RESOURCE for r in running if r not in done)
    for s in state["plan"]["steps"]:
        if s["id"] in done or s["id"] in running:
            continue
        if all(d in done for d in s.get("depends_on") or []):
            if s["agent_id"] in _SINGLE_RESOURCE:
                if busy_single:
                    continue
                busy_single = True
            out.append(s)
    return out[: cfg.MAX_PARALLEL_AGENTS]


_SINGLE_RESOURCE = {"desktop_agent"}   # agents that share one physical resource (the user's screen)


def _new_steer(state: MainState, run) -> list:
    return (getattr(run, "steer", None) or [])[state.get("steer_seen", 0):]


def _apply_steer(ctx, state: MainState) -> dict:
    """The user sent orders while the plan runs ("change this part", "skip that", "also do X"): re-plan the
    remaining steps, and add redo steps for finished work they want changed. Returns state updates ({} if none)."""
    new = _new_steer(state, ctx.run)
    if not new:
        return {}
    plan, reps = state["plan"], state.get("reports") or {}
    done = [s for s in plan["steps"] if s["id"] in reps]
    pending = [s for s in plan["steps"] if s["id"] not in reps]
    msgs = "\n".join(f"- {m['text']}" for m in new)
    ctx.node("steer", "Changing course: " + new[-1]["text"][:80])
    active = {a.id for a in registry.list_agents(False)}

    def fallback():   # no LLM: keep the plan, but every pending step carries the new orders
        return {"steps": [{**s, "instruction": s["instruction"] + "\n\nUSER UPDATE: " + msgs} for s in pending],
                "note": "added your update to the remaining steps"}

    out = ctx.llm().json(
        "You are the Main Agent. The user sent NEW ORDERS while the plan was running. They take priority over the "
        "original request. Rewrite the REMAINING plan: edit pending steps, drop steps the user no longer wants, add "
        "new steps (new unique ids like 'u1'), and add REDO steps for finished work the user wants changed (a redo "
        "step depends_on the finished step so it gets its output, and its instruction says exactly what to change). "
        "Keep finished work that the user did not ask to change. Use only the listed agent ids or 'spawn' (+role).\n"
        'Return {"steps": [{"id": str, "agent_id": str, "instruction": str, "depends_on": [ids]}], '
        '"note": "one line: what changed"}',
        f"Original request: {state['request']}\n\nNEW ORDERS FROM THE USER:\n{msgs}\n\n"
        f"Finished steps: {json.dumps([{'id': s['id'], 'agent_id': s['agent_id'], 'instruction': s['instruction'][:300], 'summary': (reps[s['id']].get('summary') or '')[:200]} for s in done])}\n"
        f"Pending steps: {json.dumps([{k: s.get(k) for k in ('id', 'agent_id', 'instruction', 'depends_on')} for s in pending])}\n"
        f"Agents: {sorted(active)[:60]}",
        fallback, role=P(state)["model_role"], max_tokens=P(state)["max_tokens"] + 400,
        validate=lambda d: isinstance(d.get("steps"), list) and all(
            s.get("id") and (s.get("agent_id") in active or (s.get("agent_id") == "spawn" and s.get("role")))
            for s in d["steps"]))
    known = {s["id"] for s in done}
    steps = []
    for s in out["steps"]:
        if s["id"] in known:            # never overwrite a finished step: it becomes a new redo step
            s = {**s, "id": s["id"] + "u", "depends_on": [s["id"]]}
        s["depends_on"] = [d for d in s.get("depends_on") or [] if d in known or d in {x["id"] for x in out["steps"]}]
        known.add(s["id"])
        steps.append(s)
    new_plan = {**plan, "steps": done + steps}
    ctx.emit(f"Changed course for your message: {out.get('note') or 'plan updated'}", steer=True)
    _plan_event(ctx, new_plan, reps)
    return {"plan": new_plan, "steer_seen": state.get("steer_seen", 0) + len(new),
            "request": state["request"] + "\n\nUSER UPDATE (sent mid-task, takes priority):\n" + msgs}


def _unusable(r: dict) -> bool:
    """A finished step whose output can't feed the next step (failed, or partial with nothing produced)."""
    if r.get("status") == "failed":
        return True
    return r.get("status") == "partial" and not str(r.get("result") or "").strip() and not r.get("files_created")


def _blocked_reports(state: MainState) -> dict:
    """Steps whose input steps failed are NOT run blind (a solver with no problem statement, a paste with no code):
    they get a 'blocked' failed report so the review re-plans the missing input with a different strategy."""
    from ..models import Report
    reps, running = state.get("reports") or {}, set(state.get("running") or [])
    out = {}
    for s in state["plan"]["steps"]:
        deps = s.get("depends_on") or []
        if s["id"] in reps or s["id"] in running or not deps or not all(d in reps for d in deps):
            continue
        bad = [d for d in deps if _unusable(reps[d])]
        if bad:
            out[s["id"]] = Report(agent_id=s["agent_id"], task_id=s["id"], status="failed",
                                  summary=f"Not run: its input step(s) {', '.join(bad)} failed, so it had nothing to "
                                          f"work with", errors=[f"blocked: dependency {', '.join(bad)} failed"]
                                  ).model_dump()
    return out


def dispatch(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    upd = _apply_steer(ctx, state)
    if upd:
        state = {**state, **upd}
    blocked = _blocked_reports(state)
    if blocked:
        ctx.emit("Not running " + ", ".join(blocked) + ": their inputs failed — re-planning those first",
                 blocked=list(blocked))
        state = {**state, "reports": {**(state.get("reports") or {}), **blocked}}
        upd = {**upd, "reports": blocked}
    ready = _ready(state)
    ctx.node("dispatch", "Delegating: " + ", ".join(s["agent_id"] for s in ready) if ready else "Nothing to run")
    _plan_event(ctx, state["plan"], state.get("reports"), [s["id"] for s in ready])
    return {**upd, "running": [s["id"] for s in ready]}


def fan_out(state: MainState):
    from .. import routing
    if routing.over_budget(state["task"].get("run_id", ""), state.get("effort", 2)):
        return "review"   # token budget spent: stop delegating, answer with what we have
    ready = [s for s in state["plan"]["steps"] if s["id"] in set(state.get("running") or [])
             and s["id"] not in (state.get("reports") or {})]
    if not ready:
        return "review"
    reports = state.get("reports") or {}
    lvl = state.get("effort", 2)

    def slim(r: dict) -> dict:   # pass only what downstream agents need, trimmed to the tier's budget
        res = r.get("result")
        res = res if isinstance(res, str) else json.dumps(res, default=str)
        return {"agent_id": r["agent_id"], "status": r["status"], "summary": r.get("summary", ""),
                "result": tiers.trim(res, lvl), "files_created": r.get("files_created", []),
                "files_modified": r.get("files_modified", [])}

    from ..core import execstate
    xs = state.get("xstate") or {}
    return [Send("run_step", {"step": s, "context": [slim(reports[d]) for d in s.get("depends_on") or []
                                                     if d in reports] + ([execstate.as_context(xs)] if xs else []),
                              "request": state["request"], "effort": s.get("effort", -1) if s.get("effort", -1) >= 0 else lvl,
                              "handoff": execstate.handoff(xs, s) if xs else ""}) for s in ready]


def run_step(payload: dict, config) -> dict:
    ctx = get_ctx(config)
    s = payload["step"]
    instr = s["instruction"]
    if payload["request"] not in instr:
        instr += f"\n\n(Overall user request, for context: {payload['request']})"
    if payload.get("handoff"):    # receiving agent continues from the real state instead of starting over
        instr += "\n\n" + payload["handoff"]
    if s["agent_id"] == "spawn":
        rep = _spawn_worker(ctx, s, instr, payload)
    else:
        rep = ctx.delegate(s["agent_id"], instr, context=payload["context"], effort=payload.get("effort"))
    return {"reports": {s["id"]: rep.model_dump()}}


def _spawn_worker(ctx, s: dict, instr: str, payload: dict):
    """Create a temporary specialist on the fly (not registered), like a forked sub-agent, and run it."""
    from ..models import AgentSpec, AgentTask, Permissions
    from .. import routing
    if routing.over_budget(ctx.run.run_id, payload.get("effort", 2)):
        from ..models import Report
        return Report(agent_id="spawn", task_id=s["id"], status="failed", summary="Skipped: token budget reached")
    role = (s.get("role") or "You are a focused specialist.").strip()
    name = (s.get("name") or role.split(".")[0].replace("You are", "").strip(" a")[:40] or "Worker").title()
    spec = AgentSpec(id=f"spawn_{ctx.run.run_id}_{s['id']}", name=f"⚡ {name}", type="custom", category="Spawned",
                     description=role[:200], graph="generic", system_prompt=role,
                     tools=["web_search", "web_fetch", "memory_recall", "fs_read", "fs_list", "search_code"],
                     permissions=Permissions(network=["*"]), created_by="main_agent:spawn")
    task = AgentTask(task_id=s["id"], agent_id=spec.id, instruction=instr, context=payload["context"],
                     run_id=ctx.run.run_id, parent_agent="main_agent", depth=ctx.task.depth + 1,
                     effort=payload.get("effort", 2))
    return ctx.runtime.run_agent(task, ctx.run, spec=spec)


def collect(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    reps = state.get("reports") or {}
    ctx.node("collect", f"Collected {len(reps)} report(s)")
    _plan_event(ctx, state["plan"], reps)
    from ..core import execstate
    xs = _sync_todo(state, state["plan"], reps)
    for r in reps.values():                       # merge each agent's returned state (page, profile, failures)
        xs = execstate.merge(xs, execstate.from_report(r))
    ctx.emit("TODO: " + ", ".join(f"{t['id']}={t['status']}{'✓' if t.get('verified') else ''}"
                                  for t in xs.get("todo", [])), todo=xs.get("todo", []))
    return {"xstate": xs}


def after_collect(state: MainState):
    from .. import events as _ev
    if _ready(state) or _new_steer(state, _ev.get(state["task"].get("run_id", ""))):
        return "dispatch"     # ready steps, or the user sent new orders → re-plan in dispatch
    return "review"


def review(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("review", "Reviewing the agents' reports")
    plan, reps = state["plan"], state.get("reports") or {}
    it = state.get("iterations", 0) + 1
    failed = [s for s in plan["steps"] if reps.get(s["id"], {}).get("status") == "failed"]

    def fallback():
        retry = [] if it > 1 else [{"id": f"{s['id']}r", "agent_id": s["agent_id"],
                                    "instruction": s["instruction"] + "\nPrevious attempt FAILED because: "
                                    + "; ".join(reps[s["id"]].get("errors", [])[:2])[:400]
                                    + "\nUse a DIFFERENT strategy than before; continue from the current state, "
                                      "do not redo completed work.", "depends_on": []}
                                   for s in failed if not _non_retryable(reps[s["id"]])]
        return {"satisfied": not retry, "issues": [f"{s['agent_id']} failed" for s in failed], "followups": retry}

    brief = {sid: {k: r.get(k) for k in ("agent_id", "status", "summary", "errors", "recommendations")}
             | {"result": str(r.get("result"))[:1500],
                "attachments": [f"{a.get('kind')}: {a.get('title')}" for a in r.get("artifacts") or []]}
             for sid, r in reps.items()}
    ids = {a.id for a in registry.list_agents(False)}
    loops = P(state)["review_loops"]
    if loops == 0 or P(state)["level"] <= 1:
        # cheap tiers: no LLM review call — only retry hard failures once (deterministic, zero tokens)
        d, it_limit = fallback(), max(loops, 1)
    else:
        it_limit = loops
        d = ctx.llm().json(
        "You are the reviewer. Check the agents' reports against the user's request and success criteria. "
        "COUNT deliverables: if the user asked for N items and fewer were delivered, it is NOT satisfied — add "
        "follow-up steps (one per missing item). If "
        "something important is missing or wrong AND another agent run would fix it, add follow-up steps (same "
        "schema as plan steps, unique new ids, only existing agent ids). Status 'partial' means the agent could NOT "
        "verify its result. For a failed/unverified step: say WHY it failed, and the follow-up must use a DIFFERENT "
        "strategy (never the same instruction again) and continue from the current state — never redo steps that "
        "are already done and verified. A step 'blocked' because its input failed is re-done AFTER a follow-up "
        "that gets that input another way. ALTERNATIVE ROUTES before giving up: if clicking around a site failed, "
        "get the information another way (web_research_agent / web fetch of the page or the site's public API, a "
        "direct URL, search) and use the screen only for what needs the user's login (paste, submit). NEVER plan "
        "'ask the user to do it manually' while another route remains. Otherwise mark satisfied.\n"
        'Return {"satisfied": bool, "issues": [..], "followups": [{"id","agent_id","instruction","depends_on"}]}',
        f"Request: {state['request']}\nSuccess criteria: {plan.get('success_criteria')}\n"
        f"Failed attempts so far: {json.dumps((state.get('xstate') or {}).get('failed', [])[-8:], default=str)[:1500]}\n"
        f"Reports: {json.dumps(brief, default=str)[:12000]}", fallback,
        validate=lambda x: "satisfied" in x and all(f.get("agent_id") in ids for f in x.get("followups") or []),
        max_tokens=P(state)["max_tokens"], role=P(state)["model_role"])
    # MISTAKES.txt M5: the reviewer can't call it "satisfied" when NOTHING succeeded and a retry is still possible
    if d.get("satisfied") and failed and not any(r.get("status") == "success" for r in reps.values()) \
            and any(not _non_retryable(reps[s["id"]]) for s in failed):
        ctx.emit("Review said satisfied, but every step failed — retrying with a different strategy", status="failed")
        fb = fallback()
        d = {**d, "satisfied": False, "followups": d.get("followups") or fb["followups"],
             "issues": (d.get("issues") or []) + fb["issues"]}
    follow = [] if it > min(it_limit, cfg.MAX_REVIEW_LOOPS) else (d.get("followups") or [])
    existing = {s["id"] for s in plan["steps"]}
    follow = _order_followups(follow, existing)
    follow = _no_repeat_plans(follow, plan["steps"], reps)
    # don't retry what cannot succeed right now (user busy, denied, not logged in, budget spent, loop guard)
    blocked_agents = {reps[s["id"]]["agent_id"] for s in failed if _non_retryable(reps[s["id"]])}
    follow = [f for f in follow if f.get("agent_id") not in blocked_agents]
    follow = _affordable_polish(ctx, state, reps, follow)
    follow = [_prefer_vm_browser(f, state.get("request", "")) for f in follow]   # same routing rule as the plan
    # SIMILARITY STOP: if this round's outcome is (nearly) the same as the previous round's, more rounds won't help
    sig = _outcome_sig(reps)
    prev_sigs = list(state.get("outcome_sigs") or [])
    if follow and prev_sigs and _similar(sig, prev_sigs[-1]) >= 0.95:   # only near-identical rounds (case-insensitive)
        ctx.emit("Same outcome as the previous round (no progress) — stopping the replanning chain", status="failed")
        follow = []
    ctx.emit("Review: " + ("satisfied" if not follow else f"{len(follow)} follow-up step(s)"),
             issues=d.get("issues", []))
    if follow:
        plan = dict(plan, steps=plan["steps"] + [PlanStep.model_validate(f).model_dump() for f in follow])
        _plan_event(ctx, plan, reps)
    return {"iterations": it, "review": d, "plan": plan, "xstate": _sync_todo(state, plan),
            "outcome_sigs": prev_sigs + [sig]}


_SCREEN_AGENTS = {"browser_agent", "desktop_agent"}


def _order_followups(follow: list[dict], existing: set) -> list[dict]:
    """Give follow-ups unique ids and KEEP their order. T1: 'f1 rewrite the code → f2 paste it → f3 submit' lost the
    links between follow-ups (only links to OLD steps were kept), so submit ran before the paste and the paste before
    the code existed. Kept: declared links to old steps or other follow-ups; links named in the text ('from step
    f1'); and screen agents (browser/desktop) act one after another, in the listed order."""
    rename = {f["id"]: (f["id"] if f["id"] not in existing else f"{f['id']}_{uuid.uuid4().hex[:4]}") for f in follow}
    known = existing | set(rename.values())
    out, last_screen = [], None
    for f in follow:
        deps = [rename.get(x, x) for x in f.get("depends_on") or []]
        deps += [rename[o] for o in rename if o != f["id"]
                 and re.search(rf"\b(step\s+)?{re.escape(o)}\b", f.get("instruction", ""), re.I)
                 and list(rename).index(o) < list(rename).index(f["id"])]
        if f.get("agent_id") in _SCREEN_AGENTS and last_screen:
            deps.append(last_screen)
        nid = rename[f["id"]]
        out.append(dict(f, id=nid, depends_on=list(dict.fromkeys(d for d in deps if d in known and d != nid))))
        if f.get("agent_id") in _SCREEN_AGENTS:
            last_screen = nid
    return out


MAX_POLISH_STEPS = 2


def _affordable_polish(ctx, state: MainState, reps: dict, follow: list[dict]) -> list[dict]:
    """BUDGET-AWARE REVIEW: when EVERY step succeeded, follow-ups are polish, not repairs — keep at most
    MAX_POLISH_STEPS and only what still fits this request's budget (seen: a weekend-trip plan whose 4 good steps
    got 6 more, 61k estimate → 284k used). Failed/partial steps and missing batch items are never cut here."""
    if not follow or not reps or any(r.get("status") != "success" for r in reps.values()) \
            or _batch_size(state["request"]):
        return follow
    from .. import routing
    rid = ctx.run.run_id
    left = routing.budget_for(rid, P(state)["level"]) - routing.run_tokens(rid)
    keep = []
    for f in follow[:MAX_POLISH_STEPS]:
        est = _est(f["agent_id"])
        if est <= left:
            keep.append(f)
            left -= est
    if len(keep) < len(follow):
        ctx.emit(f"Review: kept {len(keep)} of {len(follow)} optional follow-up(s) — every step already succeeded "
                 f"and the rest would exceed this request's budget")
    return keep


def after_review(state: MainState) -> str:
    from .. import events as _ev
    if _new_steer(state, _ev.get(state["task"].get("run_id", ""))):
        return "dispatch"     # new orders arrived during review: re-plan before answering
    return "dispatch" if _ready(state) else "synthesize"


def synthesize(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("synthesize", "Writing the final answer")
    plan, reps = state.get("plan") or {}, state.get("reports") or {}
    if state.get("final"):
        return {}
    if not reps:
        return {"final": plan.get("direct_answer") or state.get("build_failed")
                or "I couldn't find or build an agent for that. Try rephrasing, or create one in the Builder."}

    def fallback():
        parts = []
        for sid, r in reps.items():
            name = (registry.get_agent(r["agent_id"]) or type("x", (), {"name": r["agent_id"]})).name
            icon = "✅" if r["status"] == "success" else ("⚠️" if r["status"] == "partial" else "❌")
            res = r.get("result")
            res_s = res if isinstance(res, str) else json.dumps(res, indent=2, default=str)
            parts.append(f"### {icon} {name}\n{r['summary']}\n\n{res_s[:3000] if res_s and res_s != r['summary'] else ''}")
            if r.get("recommendations"):
                parts.append("**Recommendations:**\n" + "\n".join(f"- {x}" for x in r["recommendations"]))
        return "\n\n".join(parts)

    # token saver: a single successful report at a cheap tier is already the answer — no re-writing pass
    if len(reps) == 1 and P(state)["level"] <= 1:
        only = next(iter(reps.values()))
        if only["status"] == "success" and isinstance(only.get("result"), str) and only["result"].strip():
            ctx.run.emit("token", "main_agent", "running", "", text=only["result"])
            return {"final": only["result"]}
    payload = {sid: {k: r.get(k) for k in ("agent_id", "status", "summary", "recommendations",
                                             "files_created", "files_modified", "errors")}
               | {"result": tiers.trim(r.get("result") if isinstance(r.get("result"), str)
                                       else json.dumps(r.get("result"), default=str), state.get("effort", 2)),
                  # attachments the UI shows under the answer (images/diffs) — titles only, never the bytes
                  "attachments": [f"{a.get('kind')}: {a.get('title')}" for a in r.get("artifacts") or []]}
               for sid, r in reps.items()}
    text = _stream_final(
        ctx, _persona(ctx, state) + "Combine the specialised agents' reports into ONE clear, well-structured markdown "
        "answer for the user. Lead with the direct answer/outcome, then key details, files changed and next steps. "
        "Be honest about failures. Don't mention internal step ids. Files listed in files_created/attachments "
        "(e.g. screenshots under /shared/screenshots/) WERE saved: name their exact paths and never claim a "
        "screenshot or file was not saved when a path is listed. VERIFICATION RULE: status 'success' = verified; "
        "'partial' = attempted but NOT verified; 'failed' = failed. Only say something is done/submitted/sent when its "
        "status is success. For partial/failed items say clearly that it is NOT verified/not done, why, and what "
        "the user can do. Never ask 'should I continue?' for work the request already asked for. Do NOT end with a "
        "'still to do / next steps' list and NEVER ask the user questions in the answer text (questions are only "
        "asked through separate question cards). NEVER mention token budgets, token limits or cost constraints.",
        f"User request: {state['request']}\nTODO status: "
        f"{json.dumps([{k: t.get(k) for k in ('id', 'title', 'status', 'verified')} for t in (state.get('xstate') or {}).get('todo', [])], default=str)[:2500]}"
        f"\n\nReports:\n{json.dumps(payload, default=str)}", fallback,
        role=P(state)["model_role"], max_tokens=P(state)["max_tokens"])
    text, q = split_text_question(text)
    if q:   # hard rule: questions reach the owner ONLY as cards — the choice is carried out as a follow-up
        text = _question_to_card(ctx, q, text)
    return {"final": text}


_Q_TAIL_RE = re.compile(r"(?i)(which (would you|do you) (prefer|like|want)|would you like|do you want me to|"
                        r"shall i|should i|let me know (if|which|what|how)|what would you like|please (choose|pick|"
                        r"confirm|tell me))")
_OPTION_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\**\s*option\s+([a-z0-9])\b\**[\s:.)\-–—]*\**\s*(.+?)\**\s*$")


def split_text_question(text: str) -> tuple[str, dict | None]:
    """Find a question the model asked in plain text at the END of an answer (incl. 'Option A / Option B' menus).
    Returns (text without it, {"q", "options"}) — or (text, None)."""
    if not text:
        return text, None
    paras = re.split(r"\n\s*\n", text.rstrip())
    tail_start = len(paras)
    for i in range(len(paras) - 1, max(-1, len(paras) - 5), -1):   # look at the last few paragraphs only
        p = paras[i].strip()
        if p.endswith("?") or _Q_TAIL_RE.search(p) or _OPTION_RE.search(p) or re.match(r"(?i)^#+\s*next steps", p):
            tail_start = i
        else:
            break
    if tail_start == len(paras):
        return text, None
    tail = "\n\n".join(paras[tail_start:])
    opts = [m.group(2).strip(" *:") for m in _OPTION_RE.finditer(tail)][:4]
    qline = next((ln.strip(" *#") for ln in reversed(tail.splitlines()) if ln.strip().endswith("?")), "")
    if not qline and not opts:
        return text, None
    head = "\n\n".join(paras[:tail_start]).rstrip()
    return head or text, {"q": qline or "How should I continue?", "options": opts}


def _question_to_card(ctx, q: dict, text: str) -> str:
    from ..core import questions
    if getattr(ctx.run, "cancelled", False):   # the owner stopped this task: never continue it on our own
        return text
    opts = [o[:90] for o in q.get("options") or []] or ["Yes, go ahead", "No, leave it"]
    # default = the option that keeps the AI doing the work (never the 'do it manually yourself' one)
    default = next((o for o in opts if not re.search(r"(?i)\bmanual|yourself|you (click|open|type|paste)", o)), opts[0])
    ans = questions.ask(ctx.run, "main_agent", [{"q": q["q"][:300], "options": opts, "default": default}],
                        timeout=120 if _auto_mode() else 600)
    choice = (ans[0] if ans else "") or default
    if re.match(r"(?i)^\s*(no|leave it|stop|cancel)", choice):
        return text
    try:   # carry the choice out as the next message of this conversation (a new run, after this one)
        from ..core.runtime import runtime
        cid = ctx.run.conversation_id
        if cid:
            import threading
            threading.Timer(2.0, lambda: runtime.start_chat(f"{choice}", cid, notify=getattr(ctx.run, "notify", False))
                            ).start()
            return text + f"\n\n_→ Continuing with: **{choice}**_"
    except Exception:  # noqa: BLE001
        pass
    return text


def _auto_mode() -> bool:
    from .. import settings
    return settings.get("auto_mode") is not False


def remember(state: MainState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("remember", "Updating long-term memory")
    req, final = state["request"], state.get("final", "")

    def fallback():
        facts = []
        m = re.search(r"(?i)\b(remember that|my name is|i prefer|i like|i am|i'm)\b(.{3,160})", req)
        if m:
            facts.append(req.strip()[:300])
        return {"facts": facts}

    personal = re.search(r"(?i)\b(i|i'm|i am|my|me|mine|remember|prefer)\b", req)
    if not personal:   # token saver: nothing about the user in the message → skip the extraction call
        d = {"facts": []}
    else:
        d = ctx.llm().json(
            "Extract durable facts about the USER worth remembering long-term (identity, preferences, goals, "
            'projects). Ignore one-off task details. Return {"facts": ["..."]} (usually empty).',
            f"User: {req}\nAssistant: {final[:600]}", fallback,
            validate=lambda x: isinstance(x.get("facts"), list), role="worker", max_tokens=200)
    for f in d["facts"][:5]:
        ctx.memory.add(f, "fact", source="main_agent")
    reps = state.get("reports") or {}
    if reps:
        agents = ", ".join(sorted({r["agent_id"] for r in reps.values()}))
        ctx.memory.add(f"Task: {req[:200]} → handled by {agents}", "episode", source="main_agent", scope="main_agent")
        try:   # EXPERIENCE: write/merge the do / don't / how note for this kind of task
            from ..core import experience
            xs_ = state.get("xstate") or {}
            ok_ = any(r.get("status") == "success" for r in reps.values())
            sid = experience.record(req, sorted({r["agent_id"] for r in reps.values()}), xs_,
                                    "success" if ok_ else "failed")
            ctx.emit(f"Experience note updated: {sid}")
        except Exception as e:  # noqa: BLE001 - learning must never break a run
            ctx.emit(f"experience note not written: {e}")
    status = "failed" if state.get("llm_down") else "success"
    if reps and all(r.get("status") == "failed" for r in reps.values()):
        status = "failed"
    xs = state.get("xstate") or {}
    if xs and any(t.get("status") in ("failed", "pending", "unverified") or not t.get("verified")
                  for t in xs.get("todo", [])):
        status = "partial" if status == "success" else status
    try:   # SELF-LEARNING: why did this run fail / waste effort? → lessons the next run on these sites reads
        from .. import routing
        from ..core import postmortem
        est = sum(int(s.get("est_tokens") or 0) for s in (state.get("plan") or {}).get("steps") or [])
        postmortem.record(ctx.run, req, status, est, routing.run_tokens(ctx.run.run_id))
    except Exception as e:  # noqa: BLE001 - learning must never break a run
        ctx.emit(f"post-mortem skipped: {e}")
    return {"report": ctx.report(final[:300], result=final, status=status,
                                 artifacts=([{"kind": "state", "title": "task state", "content": xs}] if xs else [])
                                 + [a for r in reps.values() for a in r.get("artifacts", [])
                                    if a.get("kind") != "state"][:20],
                                 files_created=[f for r in reps.values() for f in r.get("files_created", [])],
                                 files_modified=[f for r in reps.values() for f in r.get("files_modified", [])])}


# ------------------------------------------------------------------------------------------------ graph
@archetype("main", "Main Agent: intent → capabilities → registry discovery → DAG plan → (build missing agent) → "
                   "parallel delegation → review loop → synthesis → memory")
def build(spec) -> StateGraph:
    g = StateGraph(MainState)
    for name, fn in [("load_context", load_context), ("classify_intent", classify_intent), ("respond", respond),
                     ("schedule_task", schedule_task), ("manage_agents", manage_agents),
                     ("analyze_capabilities", analyze_capabilities), ("ask_user", ask_user),
                     ("discover_agents", discover_agents),
                     ("plan_dag", plan_dag), ("refine_plan", refine_plan), ("build_agent", build_agent),
                     ("risk_gate", risk_gate),
                     ("dispatch", dispatch), ("run_step", run_step), ("collect", collect), ("review", review),
                     ("synthesize", synthesize), ("remember", remember)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "classify_intent")
    g.add_conditional_edges("classify_intent", lambda s: {
        "chat": "respond", "schedule": "schedule_task", "manage": "manage_agents",
        "clarify": "remember", "command": "remember"}.get(
            "task" if s["intent"] in ("chat", "clarify") and _ASK_ME.search(s.get("request", "")) else s["intent"],
            "analyze_capabilities"),
        ["respond", "schedule_task", "manage_agents", "analyze_capabilities", "remember"])
    for n in ("respond", "schedule_task", "manage_agents"):
        g.add_edge(n, "remember")
    g.add_edge("analyze_capabilities", "ask_user")
    g.add_edge("ask_user", "discover_agents")
    g.add_edge("discover_agents", "plan_dag")
    g.add_edge("plan_dag", "refine_plan")
    g.add_conditional_edges("refine_plan", route_after_plan, ["build_agent", "risk_gate", "synthesize"])
    g.add_edge("build_agent", "discover_agents")
    g.add_edge("risk_gate", "dispatch")
    g.add_conditional_edges("dispatch", fan_out, ["run_step", "review"])
    g.add_edge("run_step", "collect")
    g.add_conditional_edges("collect", after_collect, ["dispatch", "review"])
    g.add_conditional_edges("review", after_review, ["dispatch", "synthesize"])
    g.add_edge("synthesize", "remember")
    g.add_edge("remember", END)
    return g
