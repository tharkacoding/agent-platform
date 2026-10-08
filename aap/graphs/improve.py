"""Self-Improvement — survey similar products → compare with our registry → propose improvements →
(optional, approval-gated) hand ONE new-agent idea to the Agent Builder. Never builds anything automatically."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from ..core.sdk import ApprovalDenied, archetype, get_ctx
from . import _common as c

DEFAULT_TARGETS = ["OpenAI Dots agent", "open-dots open source clone", "OpenClaw personal AI agent",
                   "Hermes Agent Nous Research", "Manus AI agent"]


class ImproveState(c.BaseState, total=False):
    targets: list
    findings: list
    gaps: dict
    proposals: dict
    build_result: dict


def survey(state: ImproveState, config) -> dict:
    ctx = get_ctx(config)
    n = max(1, int(c.budget(ctx).get("research_queries", 3)))
    targets = ctx.spec.config.get("targets") or DEFAULT_TARGETS
    named = [t for t in targets if t.split()[0].lower() in c.instruction(state).lower()]
    targets = (named or targets)[:n]
    ctx.node("survey", f"Studying {len(targets)} similar product(s)")

    def study(t: str) -> dict:
        res = c.tool(ctx, "web_search", query=f"{t} features architecture", n=4)
        hits = res.get("results", []) if c.ok(res) else []
        texts = []
        for h in hits[:2]:
            page = c.tool(ctx, "web_fetch", url=h.get("url", ""), max_chars=8000) if h.get("url") else {}
            texts.append(str(page.get("text", h.get("snippet", ""))) if c.ok(page) else h.get("snippet", ""))
        return {"product": t, "sources": [h.get("url") for h in hits[:2]], "notes": "\n".join(texts)[:9000],
                "error": res.get("error") if isinstance(res, dict) else None}

    with ThreadPoolExecutor(max_workers=4) as ex:
        findings = list(ex.map(study, targets))
    return {"targets": targets, "findings": findings}


def compare(state: ImproveState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("compare", "Comparing with our agent ecosystem")
    ours = [{"id": s.id, "name": s.name, "capabilities": s.capabilities[:6]} for s in ctx.runtime.list_specs()]
    fb = {"they_have": [], "we_lack": ["more sourced data needed (web tools offline)"] if not any(
        f["notes"] for f in state["findings"]) else [], "we_do_better": []}
    out = c.ask_json(ctx, "Compare the surveyed products with our platform. Return {\"they_have\": [..], "
                          "\"we_lack\": [..], \"we_do_better\": [..]}.",
                     f"Surveyed:\n{c.short(state['findings'], 12000)}\n\nOur agents:\n{json.dumps(ours)[:5000]}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("we_lack"), list))
    return {"gaps": out}


def propose(state: ImproveState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("propose", "Writing improvement proposals")
    fb = {"features": [f"Close gap: {g}" for g in state["gaps"].get("we_lack", [])][:5],
          "prompt_changes": [], "new_agents": []}
    out = c.ask_json(ctx, ("Propose concrete improvements. Return {\"features\": [..], \"prompt_changes\": [..], "
                           "\"new_agents\": [{\"name\", \"description\", \"why\"}]} — at most 5 of each, prioritised."),
                     f"Gaps: {json.dumps(state['gaps'])}\nRequest: {c.instruction(state)}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("features"), list))
    c.emit_plan(ctx, out, "Improvement proposals")
    return {"proposals": out}


def suggest_build(state: ImproveState, config) -> dict:
    """Approval-gated: the user must explicitly accept before the Agent Builder is invoked."""
    ctx = get_ctx(config)
    ideas = [a for a in state["proposals"].get("new_agents", []) if isinstance(a, dict) and a.get("name")]
    if not ideas or "agent_builder" not in ctx.spec.uses:
        return {"build_result": {}}
    idea = ideas[0]
    ctx.node("suggest_build", f"Asking whether to build '{idea['name']}'")
    try:
        ctx.approve(f"Build suggested agent '{idea['name']}'?", {"idea": idea, "all_ideas": ideas})
    except ApprovalDenied:
        return {"build_result": {"status": "declined", "idea": idea["name"]}}
    rep = c.delegate(ctx, "agent_builder", f"Create an agent: {idea['name']} — {idea.get('description', '')}")
    return {"build_result": {"status": rep.get("status"), "summary": rep.get("summary"), "idea": idea["name"]}}


def finalize(state: ImproveState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    p, g = state["proposals"], state["gaps"]
    md = ["## Surveyed", *[f"- **{f['product']}** — {', '.join(s for s in f['sources'] if s) or 'no sources'}"
                           for f in state["findings"]],
          "", "## Gaps", *[f"- {x}" for x in g.get("we_lack", [])],
          "", "## Proposed features", *[f"- {x}" for x in p.get("features", [])],
          "", "## Prompt changes", *([f"- {x}" for x in p.get("prompt_changes", [])] or ["- none"]),
          "", "## New agent ideas", *([f"- **{a.get('name')}**: {a.get('description')} ({a.get('why', '')})"
                                       for a in p.get("new_agents", []) if isinstance(a, dict)] or ["- none"])]
    if state.get("build_result"):
        md += ["", f"Builder: {state['build_result']}"]
    offline = not any(f["notes"] for f in state["findings"])
    return {"report": ctx.report(f"{len(p.get('features', []))} feature proposals, "
                                 f"{len(p.get('new_agents', []))} new agent idea(s) from {len(state['findings'])} product(s)",
                                 result="\n".join(md), status="partial" if offline else "success",
                                 recommendations=[str(x) for x in p.get("features", [])][:5])}


@archetype("improve", "Self-improvement: survey similar products → compare → propose → approval-gated build suggestion")
def build(spec):
    g = StateGraph(ImproveState)
    for n, fn in [("survey", survey), ("compare", compare), ("propose", propose), ("suggest_build", suggest_build),
                  ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "survey")
    g.add_edge("survey", "compare")
    g.add_edge("compare", "propose")
    g.add_edge("propose", "suggest_build")
    g.add_edge("suggest_build", "finalize")
    g.add_edge("finalize", END)
    return g
