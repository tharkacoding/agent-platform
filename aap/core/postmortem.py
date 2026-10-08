"""Run post-mortem (owner, 2026-10-08): after every run AgentOS works out WHY it failed or wasted effort — from what
really happened (events), not from what the agents claimed — and writes it into its own memory as lessons that the
planner and the agents read next time (execstate.lessons_for). Deterministic: no model call, no tokens.

Categories (docs/REQUIREMENTS.md §2):
  LOOP           the supervisor caught repeats / ping-pong / no progress
  WANDERED       it left the task's site for unrelated pages
  NOT_VERIFIED   a step claimed something the page/result didn't prove
  WRONG_FORMAT   code/answer in a form the target can't use (stdin code for LeetCode, wrong editor language…)
  ENVIRONMENT    a missing tool/module/login/service, not a logic problem
  MODEL_OUTAGE   every model failed — nothing it produced is real
  OVER_BUDGET    far more tokens than estimated
  STOPPED        the user cancelled it
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

_RULES = [
    ("MODEL_OUTAGE", lambda e: e["message"].startswith("AI models unavailable")),
    ("LOOP", lambda e: e["type"] == "supervisor" and re.search(r"repeating|back and forth|no action", e["message"])),
    ("WANDERED", lambda e: e["type"] == "supervisor" and "unrelated" in e["message"]),
    ("NOT_VERIFIED", lambda e: e["message"].startswith("NOT verified")),
    ("WRONG_FORMAT", lambda e: e["message"].startswith("Not submitted") or "Compile Error" in e["message"]
     or "wrong editor language" in e["message"]),
    ("ENVIRONMENT", lambda e: re.search(r"is missing in this environment|is not on this machine|unreachable|"
                                        r"not logged in|ServiceUnavailable", e["message"])),
]
_ADVICE = {
    "LOOP": "change strategy after 2 failed tries (other element / direct URL / API / tool) instead of repeating",
    "WANDERED": "stay on the task's site; navigate only to URLs from the task or from links on the page",
    "NOT_VERIFIED": "check the real result (page text, judge verdict, file content) before reporting success",
    "WRONG_FORMAT": "match the target's format first (judge signature, editor language) before submitting",
    "ENVIRONMENT": "repair the environment (install/login/start service) or report it — don't debug correct code",
    "MODEL_OUTAGE": "models were down: retry later; never present fallback text as an answer",
    "OVER_BUDGET": "fewer, bigger steps; cheaper tier for sub-agents; stop polishing once the goal is met",
    "STOPPED": "",
}


def _domains(events: list[dict]) -> list[str]:
    out = Counter()
    for e in events:
        u = str((e.get("data") or {}).get("url") or "")
        m = re.search(r'"url":\s*"(https?://[^"]+)"', e["message"]) if not u else None
        u = u or (m.group(1) if m else "")
        h = (urlparse(u).hostname or "").removeprefix("www.")
        if h:
            out[h] += 1
    return [d for d, _ in out.most_common(2)]


def analyse(run, status: str, estimate: int = 0, used: int = 0) -> list[dict]:
    """[{category, evidence, advice}] for this run (empty = nothing worth learning)."""
    events = [e for e in run.events if e["type"] != "token"]
    found: dict[str, str] = {}
    for e in events:
        for cat, rule in _RULES:
            if cat not in found and rule(e):
                found[cat] = f"{e['agent_id']}: {e['message'][:160]}"
    if estimate and used > 2 * estimate:
        found["OVER_BUDGET"] = f"used {used:,} tokens vs estimate {estimate:,}"
    if getattr(run, "cancelled", False):
        found["STOPPED"] = "cancelled by the user"
    if status == "success" and not ({"LOOP", "WANDERED", "OVER_BUDGET"} & set(found)):
        return []                      # a clean success teaches nothing new here (the experience note covers it)
    return [{"category": c, "evidence": ev, "advice": _ADVICE.get(c, "")} for c, ev in found.items()]


def record(run, request: str, status: str, estimate: int = 0, used: int = 0) -> list[dict]:
    """Analyse the finished run and save each finding as a lesson for the sites/agents involved + a global one."""
    from . import execstate
    findings = analyse(run, status, estimate, used)
    if not findings:
        return []
    scopes = _domains([e for e in run.events if e["type"] != "token"]) or ["general"]
    for f in findings:
        if f["category"] == "STOPPED":
            continue
        text = f"[{f['category']}] task '{request[:70]}': {f['evidence'][:140]}"
        for sc in scopes:
            execstate.record_lesson(sc, "failed", text, strategy=f["advice"], why=f["category"])
    run.emit("log", "main_agent", "info", "Post-mortem — what to do better next time: "
             + "; ".join(f"{f['category']}: {f['advice']}" for f in findings if f["advice"])[:400],
             postmortem=findings)
    return findings
