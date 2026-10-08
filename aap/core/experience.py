"""Experience Curator — the platform learns from every task and checks its notes before the next similar one.

After a task: write/update an *experience skill* (data/skills/exp-<topic>/SKILL.md) with
  When this applies · DO (what worked, verified) · DON'T (what failed and why) · HOW (the steps that worked)
Before a task: find the most relevant experience skills (keyword overlap with the request) and hand a compact
DO / DON'T checklist to the planner and every agent handoff.
Notes are merged per topic (no duplicates) and are normal skills: visible/editable/deletable on the Skills page.
"""
from __future__ import annotations

import json
import re
import time

from .. import config

PREFIX = "exp-"
_STOP = set("a an the and or of to for in on with my me i we you your is are be that this it as at by from please "
            "can could would should do does will use using open make get want need some then also just now".split())


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


def topic_of(request: str, agents: list[str]) -> str:
    """Stable topic key: main agent kind + the 3 most specific words (e.g. 'leetcode_agent-submit-two-sum')."""
    words = [w for w in re.findall(r"[a-z0-9]+", request.lower()) if len(w) > 3 and w not in _STOP][:3]
    head = sorted(a for a in agents if a not in ("main_agent",))[:1]
    return re.sub(r"[^a-z0-9-]+", "-", "-".join(head + words)).strip("-")[:60] or "general"


def _parse(md: str) -> dict:
    sec = {"do": [], "dont": [], "how": [], "applies": ""}
    cur = None
    for line in md.splitlines():
        h = line.strip().lower()
        if h.startswith("## when"):
            cur = "applies"
        elif h.startswith("## do") and "don" not in h:
            cur = "do"
        elif h.startswith("## don"):
            cur = "dont"
        elif h.startswith("## how"):
            cur = "how"
        elif cur == "applies" and line.strip():
            sec["applies"] += line.strip().lstrip("- ") + " "
        elif cur and line.strip().startswith("- ") and line.strip()[2:] != "(nothing yet)":
            sec[cur].append(line.strip()[2:])
    return sec


def _render(topic: str, applies: str, do: list, dont: list, how: list, uses: int) -> str:
    def bl(xs):
        return "\n".join(f"- {x}" for x in xs) or "- (nothing yet)"
    return (f"---\nname: {PREFIX}{topic}\ndescription: Experience notes for tasks like: {applies[:200]}\n"
            f"kind: experience\nuses: {uses}\nupdated: {int(time.time())}\n---\n\n"
            f"## When this applies\n{applies.strip()}\n\n## DO (worked, verified)\n{bl(do)}\n\n"
            f"## DON'T (failed — and why)\n{bl(dont)}\n\n## HOW (steps that worked)\n{bl(how)}\n")


def record(request: str, agents: list[str], xstate: dict, final_status: str, llm_notes: dict | None = None) -> str:
    """Write/merge the experience note for this task. Returns the skill id."""
    topic = topic_of(request, agents)
    d = config.SKILLS_DIR / f"{PREFIX}{topic}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    old = _parse(f.read_text(encoding="utf-8")) if f.exists() else {"do": [], "dont": [], "how": [], "applies": ""}
    uses = 1
    if f.exists():
        m = re.search(r"^uses: (\d+)", f.read_text(encoding="utf-8"), re.M)
        uses = int(m.group(1)) + 1 if m else 2
    do, dont, how = list(old["do"]), list(old["dont"]), list(old["how"])
    notes = llm_notes or {}
    for x in notes.get("do", []):
        do.append(str(x)[:200])
    for x in notes.get("dont", []):
        dont.append(str(x)[:200])
    for x in notes.get("how", []):
        how.append(str(x)[:200])
    # deterministic facts from the real execution state (always available, no LLM needed)
    for c in xstate.get("completed", [])[-4:]:
        if c.get("verified"):
            do.append(f"{c.get('summary', '')[:140]} (verified)")
    for fl in xstate.get("failed", [])[-4:]:
        dont.append(f"{(fl.get('action') or fl.get('step') or '')[:80]}: {fl.get('error', '')[:100]}")
    b = xstate.get("browser") or {}
    if b.get("profile") and final_status == "success":
        how.append(f"browser profile used: {b['profile']} ({b.get('mode') or 'mode?'})")
    dedup = lambda xs: list(dict.fromkeys(x for x in xs if x and len(x) > 6))[-10:]  # noqa: E731
    applies = old["applies"] or request[:240]
    f.write_text(_render(topic, applies, dedup(do), dedup(dont), dedup(how), uses), encoding="utf-8")
    (d / ".source").write_text("experience-curator", encoding="utf-8")
    return f"{PREFIX}{topic}"


def recall(request: str, limit: int = 2, max_chars: int = 900) -> tuple[str, list[str]]:
    """Compact DO / DON'T checklist from the most relevant experience notes (empty if none match)."""
    want = _words(request)
    if not want or not config.SKILLS_DIR.exists():
        return "", []
    scored = []
    for d in config.SKILLS_DIR.iterdir():
        f = d / "SKILL.md"
        if not d.name.startswith(PREFIX) or not f.exists():
            continue
        md = f.read_text(encoding="utf-8")
        sec = _parse(md)
        score = len(want & _words(sec["applies"] + " " + d.name.replace("-", " ")))
        if score >= 2 and (sec["do"] or sec["dont"] or sec["how"]):   # skip empty notes
            scored.append((score, d.name, sec))
    scored.sort(key=lambda x: -x[0])
    parts, ids = [], []
    for _, name, sec in scored[:limit]:
        ids.append(name)
        parts.append(f"[{name}] DO: " + "; ".join(sec["do"][-4:]) + " | DON'T: " + "; ".join(sec["dont"][-4:])
                     + (" | HOW: " + "; ".join(sec["how"][-3:]) if sec["how"] else ""))
    return "\n".join(parts)[:max_chars], ids


def as_json(skill_id: str) -> dict:  # pragma: no cover - UI helper
    f = config.SKILLS_DIR / skill_id / "SKILL.md"
    return _parse(f.read_text(encoding="utf-8")) if f.exists() else {}


def _unused() -> str:  # pragma: no cover
    return json.dumps({})
