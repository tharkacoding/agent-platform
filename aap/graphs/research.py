"""Research — decompose → gather (parallel per sub-question) → extract → synthesize → critique ⇄ revise → report."""
from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_REVISIONS = 2


class ResearchState(c.BaseState, total=False):
    questions: list
    sources: list
    notes: list
    draft: str
    critique: dict
    revisions: int


def decompose(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("decompose", "Breaking the question into sub-questions")
    q = c.instruction(state)
    nq = max(1, int(c.budget(ctx).get("research_queries", 3)))
    if nq == 1:
        qs = [q]
    else:
        out = c.ask_json(ctx, f"Split the research question into 1-{nq} focused sub-questions. Return {{\"questions\": [..]}}.",
                         f"Question: {q}\nContext:\n{c.context_text(state, 3000)}",
                         fallback=lambda: {"questions": [q]},
                         validate=lambda d: isinstance(d.get("questions"), list) and d["questions"])
        qs = [str(x) for x in out["questions"]][:nq]
    c.emit_plan(ctx, {"questions": qs}, "Research questions")
    return {"questions": qs, "revisions": 0}


def gather(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("gather", f"Gathering sources for {len(state['questions'])} question(s)")
    srcs: list = []
    # local documents first (Document Research)
    for f in ((state.get("task") or {}).get("files") or []) + c.files_in(c.instruction(state)):
        if c.can(ctx, "fs_read"):
            r = c.tool(ctx, "fs_read", path=f if f.startswith("/") else f"/workspace/{f}")
            if c.ok(r):
                srcs.append({"title": f, "url": f, "text": str(r.get("content", ""))[:15000], "q": "all"})
    for u in c.URL_RE.findall(c.instruction(state))[:3]:
        r = c.tool(ctx, "web_fetch", url=u, max_chars=15000)
        if c.ok(r):
            srcs.append({"title": r.get("title", u), "url": u, "text": str(r.get("text", r.get("content", "")))[:15000], "q": "all"})

    def per_question(q: str) -> list:
        out = []
        if not c.can(ctx, "web_search"):
            return out
        res = c.tool(ctx, "web_search", query=q, n=4)
        hits = res.get("results", []) if c.ok(res) else []
        for h in hits[:2]:
            text = h.get("snippet", "")
            if c.can(ctx, "web_fetch") and h.get("url"):
                page = c.tool(ctx, "web_fetch", url=h["url"], max_chars=12000)
                if c.ok(page):
                    text = str(page.get("text", page.get("content", text)))
            out.append({"title": h.get("title", h.get("url", "")), "url": h.get("url", ""), "text": text[:12000], "q": q})
        return out

    with ThreadPoolExecutor(max_workers=4) as ex:
        for lst in ex.map(per_question, state["questions"]):
            srcs.extend(lst)
    if (state.get("task") or {}).get("context"):
        srcs.append({"title": "upstream reports", "url": "", "text": c.context_text(state), "q": "all"})
    ctx.emit(f"Collected {len(srcs)} source(s)")
    return {"sources": srcs}


def extract(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("extract", "Extracting key claims from sources")
    srcs = state.get("sources", [])[:2 * max(1, int(c.budget(ctx).get("research_queries", 3)))]

    def one(i: int, s: dict) -> dict:
        fb = {"claims": c.sentences(s["text"], 3)}
        out = fb if not c.optional_pass(ctx) else c.ask_json(
            ctx, "Extract up to 5 factual claims relevant to the questions. Return {\"claims\": [..]}.",
            f"Questions: {state['questions']}\nSource [{i + 1}] {s['title']} {s['url']}\n{s['text'][:8000]}",
            fallback=lambda: fb, validate=lambda d: isinstance(d.get("claims"), list))
        return {"n": i + 1, "title": s["title"], "url": s["url"], "claims": [str(x) for x in out["claims"]][:5]}

    # one call per source, IN PARALLEL (was sequential: ~270 s for 10 sources); each thread keeps this run's
    # routing context (tier, run id → token accounting and the user's token cap)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(contextvars.copy_context().run, one, i, s) for i, s in enumerate(srcs)]
        notes = [f.result() for f in futs]
    return {"notes": notes}


def synthesize(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("synthesize", "Synthesizing findings" + (" (revision)" if state.get("critique") else ""))
    notes = state.get("notes", [])

    def fb() -> str:
        if not notes:
            return (f"## {c.instruction(state)[:120]}\n\nNo external sources were available (offline / no web access). "
                    "Provide documents or enable the web tools for a sourced answer.")
        body = "\n".join(f"- {cl} [{n['n']}]" for n in notes for cl in n["claims"][:3])
        refs = "\n".join(f"[{n['n']}] {n['title']} {n['url']}" for n in notes)
        return f"## Findings\n{body}\n\n## Sources\n{refs}"

    draft = c.ask_text(ctx, ("Write a well-structured research answer with inline citations like [1] that refer to the "
                             "numbered sources, then a Sources list. Distinguish facts from inference."),
                       f"Question: {c.instruction(state)}\nSub-questions: {state['questions']}\nNotes:\n"
                       f"{json.dumps(notes)[:14000]}\n\nPrevious draft critique: {state.get('critique')}",
                       fallback=fb)
    return {"draft": draft}


def critique(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    fb = {"ok": True, "issues": []}
    if not c.optional_pass(ctx) or state.get("revisions", 0) >= int(c.budget(ctx).get("revise_rounds", MAX_REVISIONS)):
        ctx.node("critique", "Skipping critique (effort budget)")
        return {"critique": fb}
    ctx.node("critique", "Critiquing the draft")
    out = c.ask_json(ctx, ("Critique the research draft. MAJOR = a claim contradicted by or missing from the sources, "
                           "a sub-question left unanswered, or a factual error. MINOR = style, depth, wording, nice-to-"
                           "have extras. Return {\"major\": [..], \"minor\": [..]}."),
                     f"Question: {c.instruction(state)}\nDraft:\n{state['draft'][:12000]}",
                     fallback=lambda: {"major": [], "minor": []},
                     validate=lambda d: isinstance(d.get("major"), list))
    # only MAJOR problems pay for another full rewrite (critics nearly always list nits: 100 s+ per round)
    major = [str(x) for x in out.get("major") or []]
    crit = {"ok": not major, "issues": major + [str(x) for x in out.get("minor") or []]}
    return {"critique": crit, "revisions": state.get("revisions", 0) + (1 if major else 0)}


def finalize(state: ResearchState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    notes = state.get("notes", [])
    status = "success" if notes else "partial"
    return {"report": ctx.report(c.sentences(state["draft"].lstrip("# "), 1)[0][:300] if state.get("draft") else "no answer",
                                 result=state["draft"], status=status,
                                 recommendations=[str(i) for i in state.get("critique", {}).get("issues", [])][:5],
                                 artifacts=[{"kind": "link", "title": n["title"], "content": n["url"]} for n in notes if n["url"]][:10])}


def after_critique(state: ResearchState) -> str:
    if state["critique"].get("ok") or state.get("revisions", 0) >= MAX_REVISIONS:
        return "finalize"
    return "synthesize"


@archetype("research", "Researcher: decompose → parallel search/fetch → extract claims → synthesize → critique/revise")
def build(spec):
    g = StateGraph(ResearchState)
    for n, fn in [("decompose", decompose), ("gather", gather), ("extract", extract), ("synthesize", synthesize),
                  ("critique", critique), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "decompose")
    g.add_edge("decompose", "gather")
    g.add_edge("gather", "extract")
    g.add_edge("extract", "synthesize")
    g.add_edge("synthesize", "critique")
    g.add_conditional_edges("critique", after_critique, ["synthesize", "finalize"])
    g.add_edge("finalize", END)
    return g
