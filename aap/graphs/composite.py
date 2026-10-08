"""Composite agent — a mini-orchestrator over the agents listed in `uses`.

plan → delegate (parallel waves respecting dependencies) → merge → review ⇄ (retry failed) → report
e.g. Software Dev = Coding + Testing + Code Review + Documentation; Research = Web + Document + Summarization.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from .. import config as cfg
from ..core.sdk import archetype, get_ctx
from . import _common as c


class CompositeState(c.BaseState, total=False):
    steps: list
    results: dict
    merged: str
    review: dict
    rounds: int


def _default_steps(ctx, text: str) -> list:
    """Pipeline order from config['pipeline'] or `uses`; each step depends on the previous one."""
    order = ctx.spec.config.get("pipeline") or ctx.spec.uses
    parallel = set(ctx.spec.config.get("parallel", []))
    steps, prev = [], []
    for i, aid in enumerate(order):
        sid = f"s{i + 1}"
        steps.append({"id": sid, "agent_id": aid, "instruction": text, "depends_on": [] if aid in parallel else prev})
        if aid not in parallel:
            prev = [sid]
    return steps


def plan(state: CompositeState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("plan", "Planning collaboration between sub-agents")
    text = c.instruction(state)
    agents = {a: (ctx.runtime.get_spec(a).description if ctx.runtime.get_spec(a) else "") for a in ctx.spec.uses}
    fb = {"steps": _default_steps(ctx, text)}
    if not c.optional_pass(ctx):   # tier 0-1: fixed pipeline, no planning call
        c.emit_plan(ctx, fb, "Sub-agent plan")
        return {"steps": fb["steps"], "results": {}, "rounds": 0}
    out = c.ask_json(ctx, (
        "Split the task between these sub-agents. Return {\"steps\": [{\"id\", \"agent_id\", \"instruction\", "
        "\"depends_on\": [ids]}]}. Steps without dependencies run in parallel. Give each agent a specific instruction."),
        f"Task: {text}\n\nAgents: {json.dumps(agents)}\n\nContext:\n{c.context_text(state)}",
        fallback=lambda: fb,
        validate=lambda d: isinstance(d.get("steps"), list) and d["steps"] and
        all(s.get("agent_id") in agents for s in d["steps"]))
    steps = out["steps"][:8]
    ids = {s["id"] for s in steps}
    for s in steps:
        s["depends_on"] = [d for d in s.get("depends_on", []) if d in ids and d != s["id"]]
    c.emit_plan(ctx, {"steps": steps}, "Sub-agent plan")
    return {"steps": steps, "results": {}, "rounds": 0}


def delegate(state: CompositeState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("delegate", "Delegating to sub-agents")
    results = dict(state.get("results", {}))
    pending = [s for s in state["steps"] if s["id"] not in results or results[s["id"]].get("status") == "failed"]
    base_ctx = list((state.get("task") or {}).get("context", []))
    guard = 0
    while pending and guard < 10:
        guard += 1
        pending_ids = {p["id"] for p in pending}
        ready = [s for s in pending if all(d in results and d not in pending_ids for d in s["depends_on"])]
        if not ready:  # broken deps: run everything left
            ready = pending

        def run(s):
            upstream = base_ctx + [results[d] for d in s["depends_on"] if d in results]
            # members work one tier below the lead (the lead aggregates + reviews): 16.4 ran 4 parallel research
            # agents at "deep" each (5 queries, 2 rewrites) → 15+ min; never below "light"
            return s["id"], c.delegate(ctx, s["agent_id"], s["instruction"], context=upstream,
                                       effort=max(1, ctx.effort - 1))

        with ThreadPoolExecutor(max_workers=cfg.MAX_PARALLEL_AGENTS) as ex:
            for sid, rep in ex.map(run, ready):
                results[sid] = rep
        done_ids = {s["id"] for s in ready}
        pending = [s for s in pending if s["id"] not in done_ids]
    return {"results": results}


def merge(state: CompositeState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("merge", "Merging sub-agent reports")
    reps = [state["results"][s["id"]] for s in state["steps"] if s["id"] in state["results"]]

    def fb() -> str:
        return "\n\n".join(f"### {r.get('agent_id')} ({r.get('status')})\n{r.get('summary', '')}\n\n"
                           f"{c.short(r.get('result', ''), 1500)}" for r in reps)

    merged = c.ask_text(ctx, "Combine these sub-agent reports into one coherent result for the task. Markdown.",
                        f"Task: {c.instruction(state)}\n\nReports:\n{c.short(reps, 14000)}", fallback=fb)
    return {"merged": merged}


def review(state: CompositeState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("review", "Reviewing combined result")
    failed = [sid for sid, r in state["results"].items() if r.get("status") == "failed"]
    rnd = state.get("rounds", 0) + 1
    if failed and rnd <= min(1, int(c.budget(ctx).get("review_loops", 1))):
        ctx.emit(f"Retrying failed steps: {failed}")
        return {"review": {"retry": True, "failed": failed}, "rounds": rnd}
    return {"review": {"retry": False, "failed": failed}, "rounds": rnd}


def finalize(state: CompositeState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    reps = list(state["results"].values())
    failed = state["review"]["failed"]
    status = "success" if not failed else ("partial" if len(failed) < len(reps) else "failed")
    recs = [r for rep in reps for r in rep.get("recommendations", [])][:10]
    files_c = sorted({f for rep in reps for f in rep.get("files_created", [])})
    files_m = sorted({f for rep in reps for f in rep.get("files_modified", [])})
    arts = [a for rep in reps for a in rep.get("artifacts", [])][:10]
    summary = f"{ctx.spec.name}: {len(reps) - len(failed)}/{len(reps)} sub-agents succeeded ({', '.join(r.get('agent_id', '?') for r in reps)})"
    return {"report": ctx.report(summary, result=state["merged"], status=status, recommendations=recs,
                                 files_created=files_c, files_modified=files_m, artifacts=arts,
                                 errors=[f"{state['results'][f].get('agent_id')}: {state['results'][f].get('summary')}"
                                         for f in failed])}


def after_review(state: CompositeState) -> str:
    return "delegate" if state["review"].get("retry") else "finalize"


@archetype("composite", "Team lead over sub-agents: plan → parallel delegation → merge → review/retry")
def build(spec):
    g = StateGraph(CompositeState)
    for n, fn in [("plan", plan), ("delegate", delegate), ("merge", merge), ("review", review), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "delegate")
    g.add_edge("delegate", "merge")
    g.add_edge("merge", "review")
    g.add_conditional_edges("review", after_review, ["delegate", "finalize"])
    g.add_edge("finalize", END)
    return g
