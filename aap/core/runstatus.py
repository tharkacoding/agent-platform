"""What is a running task doing right now? Used to ANSWER questions the owner asks a running task
("where are you opening YouTube?", "what are you doing?") without changing its course (MISTAKES.txt M1)."""
from __future__ import annotations

import re

# a status QUESTION about the task (answered, plan unchanged) — vs. an instruction phrased politely ("can you also…")
_STATUS_Q = re.compile(
    r"(?i)^\s*(where|what|why|which|when|who|how (far|long|much|many|is|are)|is it|are you|did you|have you|has it|"
    r"what's|whats|status|progress)\b")
_REQUEST = re.compile(r"(?i)^\s*(can|could|would|will) you\b|^\s*(please|also|now|then|instead|don'?t|do not|stop|"
                      r"use|open|play|make|change|go|try|switch)\b")
STOP_RE = re.compile(r"(?i)^\s*(stop|cancel|abort|quit|halt)\b[\s.!]*$")


def is_status_question(text: str) -> bool:
    t = (text or "").strip()
    if not t or _REQUEST.match(t):
        return False
    return bool(_STATUS_Q.match(t)) or (t.endswith("?") and len(t) < 120 and not re.search(r"(?i)\byou (also|now)\b", t))


def describe(run) -> str:
    """Plain-language status of a live run, from its own events (no LLM call)."""
    evs = list(getattr(run, "events", []) or [])
    if not evs:
        return "It has just started."
    plan = next((e for e in reversed(evs) if e["type"] == "plan" and e["agent_id"] == "main_agent"), None)
    node = next((e for e in reversed(evs) if e["type"] == "node"), None)
    where = next((e["message"] for e in reversed(evs) if e["type"] == "log" and re.search(
        r"(?i)real chrome profile|browser profile|chrome profile", e.get("message", ""))), "")
    tool = next((e for e in reversed(evs) if e["type"] == "tool"), None)
    lines = []
    if node:
        lines.append(f"Now: {node['agent_id'].replace('_', ' ')} — {node['message'][:140]}")
    if where:
        lines.append(f"Where: {where[:160]}")
    elif tool and tool.get("data", {}).get("output") and isinstance(tool["data"]["output"], dict):
        o = tool["data"]["output"]
        if o.get("url") or o.get("profile"):
            lines.append(f"Where: {o.get('profile') or ''} {o.get('mode') or ''} {o.get('url') or ''}".strip())
    if plan:
        steps = plan["data"]["plan"].get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        cur = next((s for s in steps if s.get("status") == "running"), None)
        lines.append(f"Plan: {done}/{len(steps)} steps done" + (f"; working on: {cur['instruction'][:120]}" if cur else ""))
    return "\n".join(lines) or "Working on it."
