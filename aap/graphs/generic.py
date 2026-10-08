"""Generic worker — prepare(skills) → plan → act ⇄ reflect → logic → synthesize → report.

Used by imported skills, simple custom agents, Builder-generated agents with a logic.py, and small system agents.
"""
from __future__ import annotations

import json

from langgraph.graph import END, START, StateGraph

from ..core.sdk import TOOLS, archetype, get_ctx
from . import _common as c

MAX_ROUNDS = 3


class GenericState(c.BaseState, total=False):
    skills_text: str
    actions: list
    observations: list
    rounds: int
    more: bool
    logic_out: dict
    answer: str


def prepare(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("prepare", "Loading skills and context")
    texts = []
    for sid in ctx.spec.skills[:5]:
        r = c.tool(ctx, "skill_read", skill_id=sid)
        if c.ok(r):
            texts.append(f"## Skill: {sid}\n{str(r.get('body', r.get('content', '')))[:6000]}")
    return {"skills_text": "\n\n".join(texts), "observations": [], "rounds": 0}


def _available(ctx) -> tuple[list, list]:
    tools = [t for t in ctx.spec.tools if t != "custom_logic" and t in TOOLS]
    return tools, list(ctx.spec.uses)


def plan(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("plan", "Deciding which tools/agents to use" + (f" (round {state['rounds'] + 1})" if state.get("rounds") else ""))
    tools, uses = _available(ctx)
    if not tools and not uses:
        return {"actions": []}
    fb_actions = []
    if state.get("rounds", 0) == 0 and "memory_recall" in tools:
        fb_actions.append({"type": "tool", "name": "memory_recall", "args": {"query": c.instruction(state)[:200], "k": 5}})
    tool_desc = {t: {"description": TOOLS[t].description, "params": list(TOOLS[t].params)} for t in tools}
    out = c.ask_json(ctx, (
        "Decide the next actions to complete the task. Return {\"actions\": [{\"type\": \"tool\", \"name\": str, "
        "\"args\": {..}} | {\"type\": \"delegate\", \"agent_id\": str, \"instruction\": str}], \"done\": bool}. "
        f"Tools: {json.dumps(tool_desc)}. Agents you may delegate to: {uses}. Use at most 4 actions. "
        "Actions only GATHER what you need: keep args short and never put the final answer/deliverable inside "
        "them (it is written in the answer step). Return an empty list if you can answer from what you already know."),
        f"Task: {c.instruction(state)}\n\nContext:\n{c.context_text(state)}\n\n{state.get('skills_text', '')[:8000]}\n\n"
        f"Observations so far:\n{c.short(state.get('observations', []), 6000)}",
        fallback=lambda: {"actions": fb_actions, "done": True},
        validate=lambda d: isinstance(d.get("actions"), list))
    acts = []
    for a in out["actions"][:4]:
        if a.get("type") == "tool" and a.get("name") in tools:
            acts.append(a)
        elif a.get("type") == "delegate" and a.get("agent_id") in uses:
            acts.append(a)
    if acts:
        c.emit_plan(ctx, {"actions": acts}, "Next actions")
    return {"actions": acts}


def act(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("act", f"Executing {len(state.get('actions', []))} action(s)")
    obs = list(state.get("observations", []))
    for a in state.get("actions", []):
        if a["type"] == "tool":
            out = c.tool(ctx, a["name"], **(a.get("args") or {}))
            obs.append({"tool": a["name"], "args": a.get("args"), "output": c.short(out, 3000)})
        else:
            rep = c.delegate(ctx, a["agent_id"], a.get("instruction") or c.instruction(state),
                             context=(state.get("task") or {}).get("context"))
            obs.append({"agent": a["agent_id"], "status": rep.get("status"), "summary": rep.get("summary"),
                        "result": c.short(rep.get("result", ""), 3000)})
    return {"observations": obs, "rounds": state.get("rounds", 0) + 1}


def reflect(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("reflect", "Checking whether more work is needed")
    max_rounds = min(MAX_ROUNDS, int(c.budget(ctx).get("max_steps", MAX_ROUNDS)))
    if c.is_mock(ctx) or not c.optional_pass(ctx) or state.get("rounds", 0) >= max_rounds or not state.get("actions"):
        return {"more": False}
    out = c.ask_json(ctx, "Is the information gathered sufficient to complete the task? Return {\"more\": bool}.",
                     f"Task: {c.instruction(state)}\nObservations:\n{c.short(state.get('observations', []), 8000)}",
                     fallback=lambda: {"more": False}, validate=lambda d: "more" in d)
    return {"more": bool(out.get("more"))}


def logic(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    if "custom_logic" not in ctx.spec.tools:
        return {"logic_out": {}}
    ctx.node("logic", "Running the agent's own logic in the sandbox")
    text = c.instruction(state) + "\n" + c.context_text(state) + "\n" + "\n".join(
        str(o.get("output") or o.get("result") or "") for o in state.get("observations", []))
    out = c.tool(ctx, "custom_logic", input={"text": text, "task": c.instruction(state),
                                             "context": (state.get("task") or {}).get("context", [])})
    if isinstance(out, dict) and isinstance(out.get("output"), dict):
        out = out["output"]
    return {"logic_out": out if isinstance(out, dict) else {"result": out}}


def synthesize(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("synthesize", "Writing the answer")
    lo = state.get("logic_out") or {}

    def fb() -> str:
        parts = []
        if lo.get("summary"):
            parts.append(str(lo["summary"]))
        if lo.get("result"):
            parts.append("```json\n" + c.short(lo["result"], 3000) + "\n```")
        if lo.get("recommendations"):
            parts.append("Recommendations:\n" + "\n".join(f"- {r}" for r in lo["recommendations"]))
        for o in state.get("observations", []):
            parts.append(f"- {o.get('tool') or o.get('agent')}: {c.short(o.get('summary') or o.get('output'), 400)}")
        return "\n\n".join(parts) or f"{ctx.spec.name} processed: {c.instruction(state)[:200]}"

    ans = c.ask_text(ctx, "Answer the task using the computed results and observations. Be specific; markdown.",
                     f"Task: {c.instruction(state)}\n\nContext:\n{c.context_text(state)}\n\n"
                     f"{state.get('skills_text', '')[:6000]}\n\nLogic output:\n{c.short(lo, 6000)}\n\n"
                     f"Observations:\n{c.short(state.get('observations', []), 8000)}", fallback=fb)
    return {"answer": ans}


def finalize(state: GenericState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    lo = state.get("logic_out") or {}
    err = lo.get("error") if isinstance(lo, dict) else None
    summary = (lo.get("summary") if isinstance(lo, dict) and lo.get("summary") else c.sentences(state["answer"], 1)[0]
               if state.get("answer") else "done")
    recs = [str(r) for r in (lo.get("recommendations") or [])][:8] if isinstance(lo, dict) else []
    return {"report": ctx.report(str(summary)[:300], result=state.get("answer", ""),
                                 status="partial" if err else "success", errors=[str(err)] if err else [],
                                 recommendations=recs or c.bullets(state.get("answer", ""), 5),
                                 artifacts=[{"kind": "table", "title": "Computed result", "content": lo.get("result")}]
                                 if isinstance(lo, dict) and lo.get("result") else [])}


def route_after_plan(state: GenericState) -> str:
    return "act" if state.get("actions") else "logic"


def route_after_reflect(state: GenericState) -> str:
    return "plan" if state.get("more") else "logic"


@archetype("generic", "General worker: plan → tools/delegation loop → optional sandboxed logic → answer")
def build(spec):
    g = StateGraph(GenericState)
    for n, fn in [("prepare", prepare), ("plan", plan), ("act", act), ("reflect", reflect), ("logic", logic),
                  ("synthesize", synthesize), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "plan")
    g.add_conditional_edges("plan", route_after_plan, ["act", "logic"])
    g.add_edge("act", "reflect")
    g.add_conditional_edges("reflect", route_after_reflect, ["plan", "logic"])
    g.add_edge("logic", "synthesize")
    g.add_edge("synthesize", "finalize")
    g.add_edge("finalize", END)
    return g
