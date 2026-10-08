"""Seriousness / effort tiers — decide how much reasoning (and how many tokens) a piece of work deserves.

The Main Agent classifies every request into a tier (in the SAME LLM call as intent classification, so it costs
nothing extra). The tier travels with every delegated AgentTask (task.effort) and every graph reads its budget via
`ctx.budget`. Users can force a tier from the chat composer (Auto / Quick / Standard / Deep / Critical).

Tier  name      typical work                                   what changes
0     trivial   greetings, one-line facts, list/enable agents   cheap 'worker' model, no planning critic, 1 agent max,
                                                               no review loop, tiny prompts/outputs
1     light     short writing, simple lookups, small edits     worker model, no critic, 1 review pass max
2     standard  normal multi-step tasks                        main model, critic for 3+ step plans, 1 review loop
3     deep      multi-file coding, research reports, builds    main/coder model, critic always, 2 review loops,
                                                               larger ReAct budgets, more fix attempts
4     critical  money/security/prod/irreversible actions       best model, critic + extra verification, 3 loops,
                                                               mandatory approvals for side effects
"""
from __future__ import annotations

import re

NAMES = ["trivial", "light", "standard", "deep", "critical"]

PROFILES: list[dict] = [
    # model_role: which settings role model to use for this tier's reasoning calls
    dict(model_role="worker", max_tokens=400, history=2, context_chars=1500, max_steps=1, review_loops=0,
         critic=False, react_steps=4, fix_attempts=0, research_queries=1, revise_rounds=0, temperature=0.2),
    dict(model_role="worker", max_tokens=900, history=4, context_chars=3000, max_steps=2, review_loops=0,
         critic=False, react_steps=8, fix_attempts=1, research_queries=2, revise_rounds=0, temperature=0.2),
    dict(model_role="main", max_tokens=1800, history=6, context_chars=6000, max_steps=4, review_loops=1,
         critic="multi", react_steps=16, fix_attempts=2, research_queries=3, revise_rounds=1, temperature=0.2),
    dict(model_role="main", max_tokens=3500, history=8, context_chars=12000, max_steps=6, review_loops=2,
         critic=True, react_steps=30, fix_attempts=3, research_queries=5, revise_rounds=2, temperature=0.2),
    dict(model_role="main", max_tokens=5000, history=10, context_chars=20000, max_steps=8, review_loops=3,
         critic=True, react_steps=40, fix_attempts=4, research_queries=6, revise_rounds=2, temperature=0.1,
         require_approval=True),
]

_CRITICAL = re.compile(r"\b(production|prod|deploy|payment|bank|money|invoice|password|credential|secret|security|"
                       r"delete|wipe|migrat\w*|legal|medical|contract|tax|send (an? )?(email|message)|purchase|buy)\b",
                       re.I)
_DEEP = re.compile(r"\b(build|implement|refactor|architecture|website|app|application|full|complete|report|"
                   r"research|analy[sz]e|debug|investigate|multi|entire|whole|agent)\b", re.I)
_TRIVIAL = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|yo|good (morning|night)|what can you do)\b", re.I)


def heuristic(text: str) -> int:
    t = text.strip()
    if _TRIVIAL.search(t) and len(t) < 60:
        return 0
    crit = len(_CRITICAL.findall(t))
    if crit:
        return 4 if crit >= 2 or len(t) > 40 else 3
    deep_hits = len(_DEEP.findall(t))
    if deep_hits >= 2 or len(t) > 600:
        return 3
    if deep_hits == 1 or len(t) > 160:
        return 2
    return 1


def clamp(level) -> int:
    try:
        return max(0, min(4, int(level)))
    except (TypeError, ValueError):
        return 2


def profile(level: int) -> dict:
    p = dict(PROFILES[clamp(level)])
    p["level"], p["name"] = clamp(level), NAMES[clamp(level)]
    return p


def trim(text: str, level: int) -> str:
    """Cut long context to the tier's budget (keeps head and tail, which carry most signal)."""
    n = profile(level)["context_chars"]
    if not text or len(text) <= n:
        return text
    head = int(n * 0.7)
    return text[:head] + f"\n…[{len(text) - n} chars trimmed]…\n" + text[-(n - head):]
