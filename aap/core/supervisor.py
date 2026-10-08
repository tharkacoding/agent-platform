"""Main-Agent supervision of running sub-agents (TODO N5 — owner, after watching T1 burn 30+ browser steps).

Every CHECK_EVERY steps a sub-agent loop REPORTS to the Main Agent (`checkpoint`): its goal, its recent actions with
their reasons and results, and the tokens it has used. The Main Agent checks:
  1. deterministic signals (no LLM, no tokens): the same action again and again, ping-pong between two pages,
     wandering to sites unrelated to the task, no successful action for a while, tokens far over the estimate;
  2. only when a signal fires: one cheap model call reads the recent actions and decides
     continue | redirect (a concrete different approach, injected into the agent's goal) | stop (Main re-plans).
Every check that finds something is shown in the run's timeline as a Main Agent "supervisor" event.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import urlparse

CHECK_EVERY = 5          # steps between checkpoints
HARD_STOP_STEPS = 24     # signals still firing this late → stop without asking a model
_tok0: dict[str, int] = {}


def _sig(a: dict) -> str:
    a = {k: v for k, v in (a or {}).items() if k not in ("why", "ref_name", "role")}
    return json.dumps(a, sort_keys=True, default=str)[:200]


def _domain(u: str) -> str:
    h = (urlparse(u or "").hostname or "").lower()
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def signals(goal: str, history: list[dict], tokens: int = 0, budget: int = 0) -> list[str]:
    """What looks wrong in the recent history (empty = healthy)."""
    out: list[str] = []
    recent = history[-8:]
    sigs = Counter(_sig(h.get("action")) for h in recent)
    rep = [s for s, n in sigs.items() if n >= 3]
    if rep:
        out.append(f"repeating the same action {max(sigs.values())}x in the last {len(recent)} steps: {rep[0][:100]}")
    urls = [h["action"].get("url", "") for h in history[-10:] if (h.get("action") or {}).get("action") in ("goto", "tab_new")]
    top2 = Counter(urls[-6:]).most_common(2)
    if len(urls) >= 4 and top2[0][1] >= 2 and sum(n for _, n in top2) >= 4:
        out.append(f"going back and forth between the same pages: {[u for u, _ in top2]}")
    start = next((h["action"].get("url") for h in history if (h.get("action") or {}).get("url")), "")
    wanted = {_domain(u) for u in re.findall(r"https?://\S+", goal or "")} | ({_domain(start)} if start else set())
    goal_words = set(re.findall(r"[a-z0-9]{2,}", (goal or "").lower()))
    stray = [u for u in urls if _domain(u) and _domain(u) not in wanted          # the site's NAME ("in" of in.com),
             and _domain(u).split(".")[0] not in goal_words]                      # not its ".com", must relate to the task
    if stray:
        out.append(f"left the task's site for an unrelated page: {stray[-1][:80]}")
    if len(history) >= 6 and not any(h.get("status") == "succeeded" for h in history[-6:]):
        out.append("no action in the last 6 steps had a visible effect")
    if budget and tokens > 1.5 * budget:
        out.append(f"used {tokens:,} tokens, far over its estimate of {budget:,}")
    return out


def checkpoint(ctx, goal: str, history: list[dict], step: int, budget: int = 0) -> dict:
    """The sub-agent reports in. Returns {"verdict": continue|redirect|stop, "note": str, "signals": [...]}."""
    from .. import routing
    key = f"{ctx.run.run_id}:{getattr(ctx.task, 'task_id', '')}"
    used = routing.run_tokens(ctx.run.run_id)
    used = used - _tok0.setdefault(key, used)
    sig = signals(goal, history, used, budget)
    if not sig:
        return {"verdict": "continue", "note": "", "signals": []}
    if step >= HARD_STOP_STEPS:
        d = {"verdict": "stop", "note": "still stuck after many steps — stopping so the Main Agent can re-plan"}
    else:
        lines = "\n".join(f"{i + 1}. {h['action'].get('action')} {json.dumps({k: v for k, v in h['action'].items() if k not in ('action', 'why')}, default=str)[:120]}"
                          f" → {h.get('status', '')} · why: {str(h['action'].get('why', ''))[:120]}"
                          for i, h in enumerate(history[-8:]))
        d = ctx.llm().json(
            "You are the Main Agent supervising one of your agents while it works. Its recent steps and the warning "
            "signs are below. Decide: \"continue\" (it is actually progressing), \"redirect\" (it is stuck or doing "
            "the wrong thing — give ONE concrete, different next approach, e.g. the exact button/URL/tool to use, "
            "or what to stop doing), or \"stop\" (it cannot succeed this way; the Main Agent will re-plan). "
            'Return {"verdict": "continue|redirect|stop", "note": "short instruction or reason"}.',
            f"Agent goal: {goal[:1500]}\nWarning signs: {sig}\nRecent steps:\n{lines}",
            fallback=lambda: {"verdict": "redirect", "note": "You are repeating yourself without progress: stop "
                                                             "repeating the last actions and try a different element, "
                                                             "tool or URL."},
            validate=lambda x: x.get("verdict") in ("continue", "redirect", "stop"), role="worker", max_tokens=300)
    d["signals"] = sig
    ctx.run.emit("supervisor", "main_agent", "failed" if d["verdict"] == "stop" else "info",
                 f"Main Agent check on {ctx.spec.id} (step {step}): {'; '.join(sig)[:220]} → {d['verdict']}"
                 + (f": {d.get('note', '')[:200]}" if d.get("note") else ""),
                 task_id=getattr(ctx.task, "task_id", ""), verdict=d["verdict"], signals=sig)
    return d


def note(hist: list, name: str, args: dict, out) -> None:
    """Record one tool call of a tool-calling (ReAct) loop in the supervisor's history format."""
    ok = isinstance(out, dict) and not out.get("error") and out.get("exit_code", 0) in (0, None)
    hist.append({"action": {"action": name, **{k: str(v)[:80] for k, v in (args or {}).items()
                                               if k not in ("content", "code", "text")}},
                 "status": "succeeded" if ok else "failed"})


def react_checkpoint(ctx, goal: str, hist: list, step: int, msgs: list) -> bool:
    """For tool-calling loops (coding/terminal/...): every CHECK_EVERY steps report to the Main Agent. A redirect is
    added to the conversation as the Main Agent's message; returns True when the Main Agent says stop."""
    if not step or step % CHECK_EVERY or not hist:
        return False
    v = checkpoint(ctx, goal, hist, step)
    if v["verdict"] == "redirect" and v.get("note"):
        from langchain_core.messages import HumanMessage
        msgs.append(HumanMessage(f"[Main Agent, supervising you] {v['note']}"))
    return v["verdict"] == "stop"
