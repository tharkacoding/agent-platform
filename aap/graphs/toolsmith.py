"""Tool Builder ("toolsmith") and Skill Writer archetypes — the platform extends ITSELF.

toolsmith: understand need → design (name, params, needs, test input) → write code → validate (AST policy) →
           self-test in the sandbox → fix loop (≤3) → create_tool (user approves the code) → verify by CALLING the
           new tool → report.
skillsmith: write SKILL.md (when to use, steps, do / don't) → create_skill → verify it loads → report.
"""
from __future__ import annotations

import json
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..core import toolfactory
from ..core.sdk import archetype, get_ctx
from . import _common as c


class TSState(TypedDict, total=False):
    task: dict
    report: dict
    design: dict
    code: str
    errors: list
    tries: int
    result: dict


def design(state: TSState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("design", "Designing the new tool")
    need = c.instruction(state)
    fb = {"name": re.sub(r"[^a-z0-9]+", "_", need.lower()).strip("_")[:30] or "custom_tool",
          "description": need[:200], "params": {"text": {"type": "string"}}, "needs": "",
          "test_input": {"text": "hello"}}
    d = c.ask_json(ctx, (
        "Design ONE small reusable tool for an AI agent platform. Return JSON: name (snake_case), description "
        "(what it does, when to use it), params (JSON-schema properties object), needs ('' for pure logic or "
        "'network' if it must call a URL), test_input (a realistic example input object)."),
        f"Need: {need}\nExisting tools: {', '.join(sorted(toolfactory.TOOLS))[:1500]}", fallback=lambda: fb,
        validate=lambda x: bool(x.get("name")) and isinstance(x.get("params"), dict), role="builder")
    d["name"] = re.sub(r"[^a-z0-9_]", "_", str(d["name"]).lower())[:40]
    return {"design": d, "tries": 0, "errors": []}


def write_code(state: TSState, config) -> dict:
    ctx = get_ctx(config)
    d = state["design"]
    ctx.node("write_code", f"Writing {d['name']} (try {state.get('tries', 0) + 1})")
    fix = f"\nPrevious attempt failed: {'; '.join(state.get('errors') or [])}" if state.get("errors") else ""
    res = c.ask_text(ctx, (
        "Write the Python code for this tool. It MUST define `def run(input: dict) -> dict` returning a JSON-"
        "serialisable dict. Allowed imports only: math, statistics, json, re, collections, itertools, functools, "
        "datetime, heapq, bisect, string, random, typing, dataclasses"
        + (", urllib.request, urllib.parse (network tool)" if d.get("needs") == "network" else "")
        + ". No open(), eval, exec, subprocess, os, getattr, dunder attributes. Return ONLY one ```python block."),
        f"Tool: {json.dumps(d)}{fix}", fallback=lambda: (
            "```python\ndef run(input: dict) -> dict:\n    return {\"echo\": input}\n```"), role="coder", soul=False)
    from .browser import extract_code
    code = extract_code(res) or res
    return {"code": code, "tries": state.get("tries", 0) + 1}


def test(state: TSState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("test", "Validating and self-testing in the sandbox")
    d = state["design"]
    errs = toolfactory.validate(d["name"], state["code"], d["params"], d.get("needs", ""))
    if errs:
        return {"errors": errs[:5]}
    r = ctx.call_tool("run_python", code=state["code"], input=d.get("test_input") or {}, timeout=30)
    if not isinstance(r, dict) or r.get("error") or r.get("ok") is False:
        return {"errors": [f"self-test failed: {str(r)[:300]}"]}
    return {"errors": []}


def after_test(state: TSState) -> str:
    if state.get("errors") and state.get("tries", 0) < 3:
        return "write_code"
    return "create" if not state.get("errors") else "finalize"


def create(state: TSState, config) -> dict:
    ctx = get_ctx(config)
    d = state["design"]
    ctx.node("create", f"Registering tool {d['name']} (needs your approval)")
    r = ctx.call_tool("create_tool", name=d["name"], description=d["description"], params=d["params"],
                      code=state["code"], needs=d.get("needs", ""), test_input=d.get("test_input") or {})
    if isinstance(r, dict) and r.get("created"):   # VERIFY: the new tool really works when called like any tool
        probe = ctx.call_tool(d["name"], **(d.get("test_input") or {}))
        r["verified_call"] = probe if isinstance(probe, dict) else {"result": probe}
        r["verified"] = isinstance(probe, dict) and not probe.get("error")
    return {"result": r if isinstance(r, dict) else {"error": str(r)}}


def finalize(state: TSState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    d, r = state.get("design") or {}, state.get("result") or {}
    if r.get("created") and r.get("verified"):
        msg = (f"New tool **{d['name']}** created and verified (called it with {json.dumps(d.get('test_input'))[:120]} → "
               f"{str(r.get('verified_call'))[:200]}). All agents can use it now.")
        return {"report": ctx.report(msg, result=msg + f"\n\n```python\n{state['code']}\n```", status="success")}
    why = r.get("error") or "; ".join(state.get("errors") or []) or "not verified"
    return {"report": ctx.report(f"Tool {d.get('name')} NOT created: {why[:200]}", result=why, status="failed",
                                 errors=[why[:300]])}


@archetype("toolsmith", "Tool Builder: design → code → validate → sandbox self-test → fix → approve → register → verify")
def build_toolsmith(spec):
    g = StateGraph(TSState)
    for n, fn in [("design", design), ("write_code", write_code), ("test", test), ("create", create),
                  ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "design")
    g.add_edge("design", "write_code")
    g.add_edge("write_code", "test")
    g.add_conditional_edges("test", after_test, ["write_code", "create", "finalize"])
    g.add_edge("create", "finalize")
    g.add_edge("finalize", END)
    return g


# ------------------------------------------------------------------------------------------- skill writer
class SKState(TypedDict, total=False):
    task: dict
    report: dict
    draft: dict
    missing: list
    fixes: int
    saved: dict


REQUIRED = ["When to use", "Steps", "DO", "DON'T", "Verification"]


def draft_skill(state: SKState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("draft", "Drafting the skill")
    need = c.instruction(state)
    fix = f"\nThe previous draft was missing sections: {state.get('missing')}" if state.get("missing") else ""
    d = c.ask_json(ctx, (
        "Write a reusable SKILL for AI agents. Return JSON: name (short), description (when to use it, 1 sentence), "
        "instructions (markdown with '## ' sections exactly: When to use, Steps, DO, DON'T, Verification)."),
        f"Skill to write: {need}{fix}\nContext:\n{c.context_text(state, 3000)}",
        fallback=lambda: {"name": need[:40], "description": need[:200], "instructions": "\n\n".join(
            f"## {s}\n- {need[:120] if s == 'When to use' else '...'}" for s in REQUIRED)},
        validate=lambda x: bool(x.get("name")) and bool(x.get("instructions")), role="worker")
    return {"draft": d, "fixes": state.get("fixes", 0) + (1 if state.get("missing") else 0)}


def check_skill(state: SKState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("check", "Checking the skill has all sections")
    body = (state.get("draft") or {}).get("instructions", "")
    missing = [s for s in REQUIRED if not re.search(rf"(?im)^#+\s*{re.escape(s)}\b", body)]
    return {"missing": missing}


def save_skill(state: SKState, config) -> dict:
    ctx = get_ctx(config)
    d = state["draft"]
    ctx.node("save", f"Saving skill {d['name']}")
    r = ctx.call_tool("create_skill", name=d["name"], description=d["description"], instructions=d["instructions"])
    return {"saved": r if isinstance(r, dict) else {"error": str(r)}}


def finalize_skill(state: SKState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    r, d = state.get("saved") or {}, state.get("draft") or {}
    ok = bool(r.get("created") and r.get("skill"))   # verified: the loader can read it back
    msg = (f"New skill **{r.get('id')}** saved and verified loadable (Skills page)." if ok
           else f"Skill not created: {r.get('error') or state.get('missing')}")
    return {"report": ctx.report(msg, result=msg + "\n\n" + d.get("instructions", "")[:2000],
                                 status="success" if ok else "failed")}


@archetype("skillsmith", "Skill Writer: draft SKILL.md → check sections (fix) → save → verify it loads")
def build_skillsmith(spec):
    g = StateGraph(SKState)
    for n, fn in [("draft", draft_skill), ("check", check_skill), ("save", save_skill), ("finalize", finalize_skill)]:
        g.add_node(n, fn)
    g.add_edge(START, "draft")
    g.add_edge("draft", "check")
    g.add_conditional_edges("check", lambda s: "draft" if s.get("missing") and s.get("fixes", 0) < 1 else "save",
                            ["draft", "save"])
    g.add_edge("save", "finalize")
    g.add_edge("finalize", END)
    return g
