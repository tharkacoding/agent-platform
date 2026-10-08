"""Stateful execution: one task state that survives agent handoffs, replanning and resume.

At every moment an agent can answer: what does the user want, what is done, what is the real current state, what
failed and why, what to do differently, and how success will be verified.

The state travels inside AgentTask.context as {"kind": "exec_state", ...}; agents return an updated copy in their
report artifacts as {"kind": "state", "content": {...}} and the Main Agent merges it.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from typing import Any

from .. import config

# ------------------------------------------------------------------------------------------------ profile
_PROFILE_RE = [
    # profile "Profile 2" / profile 'Work Main'  (quoted: anything inside the quotes)
    re.compile(r"""(?i)\bprofile\s*(?:named|called|=|:)?\s*["“']([^"”']{1,40})["”']"""),
    # Chrome's own directory names: Profile 2, Default
    re.compile(r"""(?i)\bprofile\s+((?:profile\s+\d+)|default)\b"""),
    re.compile(r"""(?i)\b(Profile\s+\d+)\b"""),
    # "use my Work profile" / "in the Challa profile"
    re.compile(r"""(?i)\b(?:use|with|in|from|on|open)\s+(?:my\s+|the\s+)?([A-Za-z0-9_.\-]{1,40})\s+(?:chrome\s+|browser\s+)?profile\b"""),
    # "profile asdf" / "chrome profile Challa"
    re.compile(r"""(?i)\bprofile\s*(?:named|called|=|:)?\s+([A-Za-z0-9_.\-]{1,40})\b"""),
]
_NOT_PROFILES = {"my", "the", "a", "an", "chrome", "browser", "this", "that", "your", "real", "own", "and", "to",
                 "then", "in", "for", "on", "with", "of", "is", "it", "same", "current", "new", "other", "another",
                 "page", "picture", "pic", "photo", "settings", "url", "link", "info", "details", "name", "bio", "logged", "signed", "login", "logged-in", "user", "users", "correct", "right", "active", "chosen", "selected", "requested", "specified", "existing", "personal", "main"}


_STANDING_RE = re.compile(r"(?i)\b(always|from now on|by default|as (the |my )?default|set (it )?as default|"
                          r"every ?time|remember (this|that|to)|make it (the )?default|permanently|in future)\b")


_FOR_SITE_RE = re.compile(r"(?i)\b(?:for|on|with|when (?:using|on|opening))\s+(?:the\s+)?([a-z0-9][a-z0-9.-]{1,40})"
                          r"(?:\.(?:com|org|io|net|dev|ai|in))?\b")
_NOT_SITES = {"me", "my", "all", "everything", "every", "web", "browsing", "now", "this", "that", "it", "default",
              "chrome", "profile", "tasks", "work", "future", "good", "use", "using", "open", "opening", "a", "an",
              "to", "in", "sites", "websites", "any", "each"}


def standing_site(text: str) -> str:
    """'always use my asdf profile for leetcode' → 'leetcode' (a SITE-scoped standing preference); '' when the
    preference is general ('always use my asdf profile')."""
    for m in _FOR_SITE_RE.finditer(text or ""):
        w = m.group(1).lower().split(".")[0]
        if w not in _NOT_SITES and not w.isdigit():
            return w
    return ""


def is_standing(text: str) -> bool:
    """Does the user want this instruction to persist beyond the current request? (else it is request-scoped)"""
    return bool(_STANDING_RE.search(text or ""))


def parse_profile(text: str) -> str:
    """'Use Chrome profile asdf' / 'use my Work profile' / 'profile "Profile 2"' -> profile name ('' if none)."""
    for rx in _PROFILE_RE:
        m = rx.search(text or "")
        if m:
            name = m.group(1).strip()
            if name and name.lower() not in _NOT_PROFILES:
                return re.sub(r"(?i)^profile\s+(\d+)$", r"Profile \1", name)
    return ""


# ------------------------------------------------------------------------------------------------ state
def new_state(request: str, profile: str = "") -> dict:
    return {
        "request": request,                 # original user request (never rewritten)
        "goal": request,                    # current goal (may be refined)
        "success_criteria": [],
        "todo": [],                         # [{id, title, agent_id, status: pending|running|done|failed|skipped,
                                            #   verified: bool, evidence, error, attempts}]
        "current_step": "",
        "browser": {"profile": profile, "mode": "", "url": "", "title": "", "tabs": []},
        "completed": [],                    # [{step, summary, verified, evidence}]
        "failed": [],                       # [{step, action, error, why, strategy_to_avoid}]
        "observations": [],                 # short, most recent last (capped)
        "actions": [],                      # [{agent, action, status: planned|attempted|succeeded|verified|failed}]
        "lessons": [],                      # lessons applied to this task
        "solutions": {},                    # slug -> {code, status, verdict, details, verified, tries} (reused, never
                                            #   rewritten unless the CODE itself failed)
        "updated_at": time.time(),
    }


def from_context(context: list[dict] | None) -> dict | None:
    for c in context or []:
        if isinstance(c, dict) and c.get("kind") == "exec_state":
            return c.get("state")
    return None


def as_context(state: dict) -> dict:
    return {"kind": "exec_state", "state": state}


def from_report(rep: dict) -> dict | None:
    for a in rep.get("artifacts") or []:
        if a.get("kind") == "state" and isinstance(a.get("content"), dict):
            return a["content"]
    return None


def merge(base: dict, update: dict | None) -> dict:
    """Merge an agent's returned state into the Main state without losing completed work."""
    if not update:
        return base
    out = copy.deepcopy(base)
    b = update.get("browser") or {}
    for k in ("mode", "url", "title", "tabs"):
        if b.get(k):
            out["browser"][k] = b[k]
    if b.get("profile") and not out["browser"].get("profile"):
        out["browser"]["profile"] = b["profile"]          # never overwrite the user's explicit profile
    for k in ("completed", "failed", "observations", "actions", "lessons"):
        seen = {json.dumps(x, sort_keys=True, default=str) for x in out.get(k, [])}
        for x in update.get(k) or []:
            s = json.dumps(x, sort_keys=True, default=str)
            if s not in seen:
                out.setdefault(k, []).append(x)
                seen.add(s)
    out["observations"] = out.get("observations", [])[-12:]
    out["actions"] = out.get("actions", [])[-40:]
    sols = dict(out.get("solutions") or {})
    for slug, s in (update.get("solutions") or {}).items():   # keep the most advanced record per problem
        if not isinstance(s, dict):
            continue
        cur = sols.get(slug)
        if cur is None or solution_rank(s) >= solution_rank(cur):
            sols[slug] = s
    out["solutions"] = sols
    out["updated_at"] = time.time()
    return out


_SOL_RANK = {"pending": 0, "failed": 1, "solved": 2, "pasted": 3, "submitted": 3, "accepted": 5}


def solution_rank(s: dict) -> int:
    return _SOL_RANK.get(s.get("status", "pending"), 0) + (1 if s.get("verified") else 0)


def handoff(state: dict, step: dict | None = None, limit: int = 1800) -> str:
    """Compact handoff packet a receiving agent reads FIRST (goal, state, profile, page, done, failed, todo)."""
    b = state.get("browser") or {}
    todo_left = [t for t in state.get("todo", []) if t.get("status") in ("pending", "running", "failed")]
    lines = [
        "## Task state (continue from here — do not redo completed work)",
        f"User request: {state.get('request', '')[:300]}",
        f"Your step: {step.get('instruction', '')[:300]}" if step else "",
        f"Success criteria: {'; '.join(state.get('success_criteria') or [])[:300]}",
        (f"Browser: profile={b.get('profile') or '(not specified)'} mode={b.get('mode') or '-'} "
         f"page={b.get('url') or '-'} tabs={len(b.get('tabs') or [])}"),
        "Completed: " + ("; ".join(f"{c.get('step')}: {c.get('summary', '')[:80]}"
                                    + (" [verified]" if c.get("verified") else " [UNVERIFIED]")
                                    for c in state.get("completed", [])[-6:]) or "nothing yet"),
        "Failed attempts (do NOT repeat these, use a different strategy): "
        + ("; ".join(f"{f.get('action') or f.get('step')}: {f.get('error', '')[:80]}"
                     for f in state.get("failed", [])[-6:]) or "none"),
        "Remaining TODOs: " + ("; ".join(f"{t.get('id')} {t.get('title', '')[:60]} ({t.get('status')})"
                                         for t in todo_left[:8]) or "none"),
        "Lessons: " + ("; ".join(state.get("lessons", [])[-4:]) or "none"),
        ("Saved solutions (REUSE the code, only redo the failed step): "
         + "; ".join(f"{slug}={s.get('status')}{'✓' if s.get('verified') else ''}"
                     + (f" ({s.get('verdict')})" if s.get("verdict") else "")
                     for slug, s in list((state.get("solutions") or {}).items())[:10]))
        if state.get("solutions") else "",
    ]
    text = "\n".join(x for x in lines if x)
    return text[:limit]


def summary_for_user(state: dict) -> dict:
    todo = state.get("todo", [])
    return {"done": sum(t.get("status") == "done" for t in todo),
            "verified": sum(bool(t.get("verified")) for t in todo),
            "failed": sum(t.get("status") == "failed" for t in todo), "total": len(todo)}


# ------------------------------------------------------------------------------------------------ lessons
_LESSONS = config.DATA_DIR / "lessons.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(_LESSONS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def record_lesson(scope: str, kind: str, text: str, strategy: str = "", why: str = "") -> None:
    """kind: worked | failed | avoid | preference. scope: a domain ('leetcode.com'), agent id, or 'user'."""
    text = text.strip()[:240]
    if not text:
        return
    with _lock:
        items = _load()
        for it in items:
            if it["scope"] == scope and it["text"] == text:
                it["count"] = it.get("count", 1) + 1
                it["ts"] = time.time()
                break
        else:
            items.append({"scope": scope, "kind": kind, "text": text, "strategy": strategy[:160], "why": why[:160],
                          "count": 1, "ts": time.time()})
        items = sorted(items, key=lambda x: -x["ts"])[:500]
        _LESSONS.write_text(json.dumps(items, indent=1), encoding="utf-8")


def lessons_for(scopes: list[str], limit: int = 5) -> list[str]:
    scopes = [s for s in scopes if s]
    out = []
    for it in _load():
        if it["scope"] in scopes:
            tag = {"avoid": "AVOID", "failed": "FAILED", "worked": "WORKED", "preference": "PREF"}.get(it["kind"], "")
            out.append(f"[{tag}] {it['text']}" + (f" → {it['strategy']}" if it.get("strategy") else ""))
        if len(out) >= limit:
            break
    return out


def all_lessons() -> list[dict]:
    return _load()


def delete_lesson(index: int) -> bool:
    with _lock:
        items = _load()
        if 0 <= index < len(items):
            items.pop(index)
            _LESSONS.write_text(json.dumps(items, indent=1), encoding="utf-8")
            return True
    return False


def action_sig(action: dict) -> str:
    a = {k: v for k, v in (action or {}).items() if k not in ("why", "dy", "ms")}
    return json.dumps(a, sort_keys=True, default=str)


def obs_sig(obs: dict) -> str:
    return json.dumps([obs.get("url"), obs.get("title"), len(obs.get("elements") or []),
                       str(obs.get("text", ""))[:400], len(obs.get("tabs") or []), obs.get("error")],
                      default=str)


def _unused(_: Any = None) -> None:  # pragma: no cover
    pass
