"""Superpowers methodology (github.com/obra/superpowers, MIT) as part of AgentOS work.

The 15 Superpowers skills are imported as normal SKILL.md skills (Skills page / POST /api/skills/import with the git
URL). This module makes the agents USE them: coding / debugging / testing / review agents get a one-line index of
the skills that fit their job and the instruction to `skill_read` the full text before real work — index only, so it
costs ~150 tokens, not the whole skill. Switch: Settings → "Use the Superpowers methodology" (`superpowers_enabled`).
"""
from __future__ import annotations

from .. import settings

# graph archetype → Superpowers skills that apply (order = when they apply)
BY_GRAPH = {
    "coding": ["test-driven-development", "systematic-debugging", "verification-before-completion",
               "requesting-code-review"],
    "debug": ["systematic-debugging", "verification-before-completion"],
    "testing": ["test-driven-development", "verification-before-completion"],
    "review": ["requesting-code-review", "receiving-code-review"],
    "builder": ["writing-plans", "test-driven-development", "verification-before-completion"],
    "planner": ["brainstorming", "writing-plans"],
}


def enabled() -> bool:
    return settings.get("superpowers_enabled") is not False


def hint(graph: str) -> str:
    """'' when off, not applicable, or the skills aren't installed; else the short index for the system prompt."""
    if not enabled() or graph not in BY_GRAPH:
        return ""
    from . import loader
    have = {s["id"]: s["description"] for s in loader.list_skills()}
    rows = [f"- {sid}: {have[sid].strip().splitlines()[0][:110]}" for sid in BY_GRAPH[graph] if sid in have]
    if not rows:
        return ""
    return ("METHODOLOGY (Superpowers skills installed — before real work call skill_read{skill_id} for the ones "
            "that apply, then follow them; evidence before claims):\n" + "\n".join(rows))
