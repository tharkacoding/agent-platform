"""Planner — goal → constraints → decompose → schedule → risks → validate ⇄ (repair) → report."""
from __future__ import annotations

import json

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c


class PlannerState(c.BaseState, total=False):
    goal: str
    constraints: dict
    tasks: list
    schedule: list
    risks: list
    check: dict
    repairs: int
    memory: list


def goal(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("goal", "Clarifying the goal")
    mem = []
    if c.can(ctx, "memory_recall"):
        r = c.tool(ctx, "memory_recall", query=c.instruction(state)[:200], k=5)
        mem = r.get("results", r.get("memories", [])) if c.ok(r) else []
    out = c.ask_json(ctx, "Restate the goal as one measurable sentence. Return {\"goal\": str}.",
                     f"Request: {c.instruction(state)}\nKnown about the user: {c.short(mem, 2000)}",
                     fallback=lambda: {"goal": c.instruction(state)[:300]}, validate=lambda d: bool(d.get("goal")))
    return {"goal": out["goal"], "memory": mem, "repairs": 0}


def constraints(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("constraints", "Identifying constraints")
    text = c.instruction(state).lower()
    import re
    weeks = re.search(r"(\d+)\s*week", text)
    hours = re.search(r"(\d+)\s*(?:h|hours?)\b", text)
    fb = {"horizon_days": int(weeks.group(1)) * 7 if weeks else 7, "hours_per_day": int(hours.group(1)) if hours else 2,
          "other": []}
    out = c.ask_json(ctx, "Extract constraints. Return {\"horizon_days\": int, \"hours_per_day\": number, \"other\": [..]}.",
                     f"Goal: {state['goal']}\nRequest: {c.instruction(state)}\nContext:\n{c.context_text(state, 3000)}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("horizon_days"), int))
    return {"constraints": out}


def decompose(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("decompose", "Breaking the goal into tasks")
    base = c.keywords(state["goal"], 5) or ["goal"]
    fb = {"tasks": [{"title": f"Work on {k}", "hours": 2, "depends_on": []} for k in base]}
    out = c.ask_json(ctx, "Decompose into 3-12 concrete tasks with hour estimates. Return {\"tasks\": [{\"title\", "
                          "\"hours\", \"depends_on\": [titles]}]}.",
                     f"Goal: {state['goal']}\nConstraints: {state['constraints']}\nContext:\n{c.context_text(state, 4000)}"
                     f"\nRepair notes: {state.get('check', {}).get('issues')}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("tasks"), list) and d["tasks"])
    return {"tasks": out["tasks"][:12]}


def schedule(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("schedule", "Scheduling")
    per_day = float(state["constraints"].get("hours_per_day") or 2)
    horizon = int(state["constraints"].get("horizon_days") or 7)
    day, used, sched = 1, 0.0, []
    for t in state["tasks"]:
        h = float(t.get("hours") or 1)
        if used + h > per_day and used > 0:
            day, used = day + 1, 0.0
        sched.append({"day": day, "task": t.get("title"), "hours": h})
        used += h
    c.emit_plan(ctx, {"schedule": sched, "horizon_days": horizon}, "Schedule")
    return {"schedule": sched}


def risks(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("risks", "Assessing risks")
    out = c.ask_json(ctx, "List the top risks and mitigations. Return {\"risks\": [{\"risk\", \"mitigation\"}]}.",
                     f"Goal: {state['goal']}\nSchedule: {json.dumps(state['schedule'])[:4000]}",
                     fallback=lambda: {"risks": [{"risk": "Falling behind schedule",
                                                  "mitigation": "Keep a buffer day each week and review progress"}]},
                     validate=lambda d: isinstance(d.get("risks"), list))
    return {"risks": out["risks"][:6]}


def validate(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("validate", "Validating plan against constraints")
    horizon = int(state["constraints"].get("horizon_days") or 7)
    last = max((s["day"] for s in state["schedule"]), default=0)
    issues = [f"plan needs {last} days but horizon is {horizon}"] if last > horizon else []
    return {"check": {"ok": not issues, "issues": issues}, "repairs": state.get("repairs", 0) + (1 if issues else 0)}


def finalize(state: PlannerState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    lines = [f"## Plan: {state['goal']}", ""]
    by_day: dict = {}
    for s in state["schedule"]:
        by_day.setdefault(s["day"], []).append(s)
    for d, items in by_day.items():
        lines.append(f"**Day {d}**: " + "; ".join(f"{i['task']} ({i['hours']:g}h)" for i in items))
    lines += ["", "## Risks"] + [f"- {r.get('risk')} → {r.get('mitigation')}" for r in state["risks"]]
    if not state["check"]["ok"]:
        lines += ["", "⚠ " + "; ".join(state["check"]["issues"])]
    return {"report": ctx.report(f"{len(state['tasks'])} tasks over {len(by_day)} day(s) for: {state['goal'][:150]}",
                                 result="\n".join(lines), status="success" if state["check"]["ok"] else "partial",
                                 recommendations=[r.get("mitigation", "") for r in state["risks"]][:5],
                                 artifacts=[{"kind": "table", "title": "Schedule", "content": state["schedule"]}])}


def after_validate(state: PlannerState) -> str:
    return "decompose" if not state["check"]["ok"] and state.get("repairs", 0) < 2 else "finalize"


@archetype("planner", "Planner: goal → constraints → tasks → schedule → risks → validate ⇄ repair")
def build(spec):
    g = StateGraph(PlannerState)
    for n, fn in [("goal", goal), ("constraints", constraints), ("decompose", decompose), ("schedule", schedule),
                  ("risks", risks), ("validate", validate), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "goal")
    g.add_edge("goal", "constraints")
    g.add_edge("constraints", "decompose")
    g.add_edge("decompose", "schedule")
    g.add_edge("schedule", "risks")
    g.add_edge("risks", "validate")
    g.add_conditional_edges("validate", after_validate, ["decompose", "finalize"])
    g.add_edge("finalize", END)
    return g
