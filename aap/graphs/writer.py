"""Writer — outline → draft → critique ⇄ revise → finalize (Writing, Documentation, Summarization)."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_REVISIONS = 2
PASS_SCORE = 8


class WriterState(c.BaseState, total=False):
    source: str
    outline: list
    draft: str
    critique: dict
    revisions: int


def outline(state: WriterState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("outline", "Outlining")
    src = c.context_text(state, 12000)
    for f in ((state.get("task") or {}).get("files") or [])[:3]:
        r = c.tool(ctx, "fs_read", path=f)
        if c.ok(r):
            src += f"\n\n--- {f}\n{str(r.get('content', ''))[:8000]}"
    mode = ctx.spec.config.get("mode", "write")
    fb = {"outline": (["Key points", "Details", "Conclusion"] if mode != "summarize" else ["TL;DR", "Key points"])}
    out = c.ask_json(ctx, f"Create an outline ({mode} task). Return {{\"outline\": [section headings]}}.",
                     f"Task: {c.instruction(state)}\nSource material:\n{src[:10000]}", fallback=lambda: fb,
                     validate=lambda d: isinstance(d.get("outline"), list) and d["outline"])
    return {"source": src, "outline": [str(x) for x in out["outline"]][:10], "revisions": 0}


def draft(state: WriterState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("draft", "Drafting" if not state.get("critique") else f"Revising (round {state['revisions']})")
    text = c.instruction(state)
    src = state.get("source", "")

    def fb() -> str:
        if ctx.spec.config.get("mode") == "summarize" and (src or len(text) > 400):
            material = src or text
            pts = c.sentences(material, 5)
            return "**TL;DR:** " + (pts[0] if pts else "") + "\n\n" + "\n".join(f"- {p}" for p in pts[1:])
        secs = "\n\n".join(f"## {h}\n{text if i == 0 else ''}".rstrip() for i, h in enumerate(state["outline"]))
        return secs

    out = c.ask_text(ctx, "Write the piece following the outline. Clear, specific, well formatted markdown. "
                          "If a critique is given, fix every issue.",
                     f"Task: {text}\nOutline: {state['outline']}\nSource:\n{src[:10000]}\n\n"
                     f"Previous draft:\n{state.get('draft', '')[:6000]}\nCritique: {state.get('critique')}", fallback=fb)
    return {"draft": out}


def critique(state: WriterState, config) -> dict:
    ctx = get_ctx(config)
    fb = {"score": 10, "issues": []}
    if not c.optional_pass(ctx) or state.get("revisions", 0) >= int(c.budget(ctx).get("revise_rounds", MAX_REVISIONS)):
        ctx.node("critique", "Skipping critique (effort budget)")
        return {"critique": fb, "revisions": state.get("revisions", 0)}
    ctx.node("critique", "Critiquing")
    out = c.ask_json(ctx, "Score the draft 1-10 for accuracy, clarity, completeness vs the task. Return "
                          "{\"score\": int, \"issues\": [..]}.",
                     f"Task: {c.instruction(state)}\nDraft:\n{state['draft'][:10000]}", fallback=lambda: fb,
                     validate=lambda d: isinstance(d.get("score"), (int, float)))
    return {"critique": out, "revisions": state.get("revisions", 0) + 1}


def finalize(state: WriterState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    first = c.sentences(state["draft"].replace("#", "").replace("*", "").strip(), 1)
    return {"report": ctx.report((first[0] if first else "Draft complete")[:300], result=state["draft"],
                                 recommendations=[str(i) for i in state.get("critique", {}).get("issues", [])][:5])}


def after_critique(state: WriterState) -> str:
    cr = state["critique"]
    if cr.get("score", 10) >= PASS_SCORE or state.get("revisions", 0) >= MAX_REVISIONS:
        return "finalize"
    return "draft"


@archetype("writer", "Writer: outline → draft → critique ⇄ revise")
def build(spec):
    g = StateGraph(WriterState)
    for n, fn in [("outline", outline), ("draft", draft), ("critique", critique), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "outline")
    g.add_edge("outline", "draft")
    g.add_edge("draft", "critique")
    g.add_conditional_edges("critique", after_critique, ["draft", "finalize"])
    g.add_edge("finalize", END)
    return g
