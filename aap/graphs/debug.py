"""Debugging — reproduce → hypothesize → investigate (loop per hypothesis) → fix (via coding agent) → confirm ⇄."""
from __future__ import annotations

import re

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_ROUNDS = 2


class DebugState(c.BaseState, total=False):
    repro_cmd: str
    repro: dict
    hypotheses: list
    h_index: int
    evidence: list
    root_cause: str
    fix_report: dict
    confirm: dict
    rounds: int
    max_rounds: int


def _cmd(text: str) -> str:
    m = re.search(r"`([^`]+)`", text) or re.search(r"(?:run|command|cmd)[:\s]+(.+)$", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def reproduce(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    cmd = ctx.spec.config.get("repro_cmd") or _cmd(c.instruction(state))
    ctx.node("reproduce", f"Reproducing with `{cmd}`" if cmd else "Reproducing (from description)")
    repro = {"ran": False, "output": c.instruction(state)}
    if cmd and c.can(ctx, "run_command"):
        r = c.tool(ctx, "run_command", cmd=cmd, timeout=180)
        if c.ok(r):
            repro = {"ran": True, "exit_code": r.get("exit_code"),
                     "output": (str(r.get("stdout", "")) + "\n" + str(r.get("stderr", "")))[-6000:]}
    return {"repro_cmd": cmd, "repro": repro, "rounds": 0,
            "max_rounds": max(1, min(MAX_ROUNDS, int(c.budget(ctx).get("review_loops", MAX_ROUNDS))))}


def hypothesize(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("hypothesize", "Forming hypotheses")
    out_txt = state["repro"]["output"]
    err = [l for l in out_txt.splitlines() if re.search(r"error|exception|traceback|failed|assert", l, re.I)][-3:]
    fb = {"hypotheses": [{"cause": e.strip()[:200], "check": "inspect the referenced code"} for e in err] or
          [{"cause": "logic error in the code path described", "check": "read the relevant function"}]}
    out = c.ask_json(ctx, "List 1-4 ranked root-cause hypotheses with how to check each. Return {\"hypotheses\": "
                          "[{\"cause\", \"check\", \"search\": optional code pattern}]}.",
                     f"Problem: {c.instruction(state)}\nReproduction output:\n{out_txt}\n"
                     f"Previous evidence: {c.short(state.get('evidence', []), 3000)}",
                     fallback=lambda: fb, validate=lambda d: isinstance(d.get("hypotheses"), list) and d["hypotheses"],
                     role="coder")
    hs = out["hypotheses"][:4]
    c.emit_plan(ctx, {"hypotheses": hs}, "Hypotheses")
    return {"hypotheses": hs, "h_index": 0}


def investigate(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    i = state["h_index"]
    h = state["hypotheses"][i]
    ctx.node("investigate", f"Checking hypothesis {i + 1}: {str(h.get('cause'))[:80]}")
    ev = {"hypothesis": h.get("cause"), "observations": []}
    pat = h.get("search") or next(iter(re.findall(r"`([^`]{3,60})`", str(h.get("check", "")))), "")
    if pat and c.can(ctx, "search_code"):
        r = c.tool(ctx, "search_code", pattern=pat)
        if c.ok(r):
            ev["observations"].append({"search": pat, "matches": r.get("matches", [])[:10]})
    for f in c.files_in(str(h.get("check", "")) + " " + str(h.get("cause", "")))[:2]:
        r = c.tool(ctx, "fs_read", path=f if f.startswith("/") else f"/workspace/{f}")
        if c.ok(r):
            ev["observations"].append({"file": f, "content": str(r.get("content", ""))[:4000]})
    verdict = c.ask_json(ctx, "Given the evidence, is this hypothesis the root cause? Return {\"confirmed\": bool, \"why\": str}.",
                         c.short(ev, 8000), fallback=lambda: {"confirmed": i == 0, "why": "most likely by ranking"},
                         validate=lambda d: "confirmed" in d, role="coder")
    ev.update(verdict)
    evidence = state.get("evidence", []) + [ev]
    root = h.get("cause") if verdict.get("confirmed") else state.get("root_cause", "")
    return {"evidence": evidence, "h_index": i + 1, "root_cause": root}


def fix(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("fix", "Fixing the root cause")
    instr = (f"Fix this bug. Root cause: {state.get('root_cause') or 'see evidence'}.\nProblem: {c.instruction(state)}\n"
             f"Reproduce with: {state.get('repro_cmd') or 'n/a'}\nEvidence: {c.short(state.get('evidence', []), 3000)}")
    target = next((u for u in ctx.spec.uses if "coding" in u or u.endswith("python_agent")), None)
    if target:
        rep = c.delegate(ctx, target, instr, context=(state.get("task") or {}).get("context"))
    else:
        rep = {"status": "partial", "summary": "No coding agent available; proposing fix only",
               "result": c.ask_text(ctx, "Propose a concrete code fix (diff).", instr, fallback=lambda: instr)}
    return {"fix_report": rep}


def confirm(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("confirm", "Confirming the fix")
    if state.get("repro_cmd") and c.can(ctx, "run_command"):
        r = c.tool(ctx, "run_command", cmd=state["repro_cmd"], timeout=180)
        okk = c.ok(r) and r.get("exit_code") == 0
        out = (str(r.get("stdout", "")) + str(r.get("stderr", "")))[-3000:] if isinstance(r, dict) else ""
    else:
        okk, out = state["fix_report"].get("status") == "success", "no reproduction command; trusting fix report"
    return {"confirm": {"ok": okk, "output": out}, "rounds": state.get("rounds", 0) + 1}


def finalize(state: DebugState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    okk = state["confirm"]["ok"]
    fr = state.get("fix_report", {})
    md = (f"**Root cause:** {state.get('root_cause') or 'not confirmed'}\n\n**Fix:** {fr.get('summary', '')}\n\n"
          f"**Verification:** {'passed' if okk else 'not confirmed'}\n\n```\n{state['confirm']['output'][-1500:]}\n```")
    return {"report": ctx.report(f"{'Fixed' if okk else 'Investigated'}: {str(state.get('root_cause') or c.instruction(state))[:200]}",
                                 result=md, status="success" if okk else "partial",
                                 files_modified=fr.get("files_modified", []),
                                 artifacts=fr.get("artifacts", [])[:5])}


def after_investigate(state: DebugState) -> str:
    if state.get("root_cause") or state["h_index"] >= len(state["hypotheses"]):
        return "fix"
    return "investigate"


def after_confirm(state: DebugState) -> str:
    return "finalize" if state["confirm"]["ok"] or state.get("rounds", 0) >= state.get("max_rounds", MAX_ROUNDS) \
        else "hypothesize"


@archetype("debug", "Debugger: reproduce → hypothesize → investigate loop → fix (delegates) → confirm ⇄")
def build(spec):
    g = StateGraph(DebugState)
    for n, fn in [("reproduce", reproduce), ("hypothesize", hypothesize), ("investigate", investigate), ("fix", fix),
                  ("confirm", confirm), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "reproduce")
    g.add_edge("reproduce", "hypothesize")
    g.add_edge("hypothesize", "investigate")
    g.add_conditional_edges("investigate", after_investigate, ["investigate", "fix"])
    g.add_edge("fix", "confirm")
    g.add_conditional_edges("confirm", after_confirm, ["finalize", "hypothesize"])
    g.add_edge("finalize", END)
    return g
