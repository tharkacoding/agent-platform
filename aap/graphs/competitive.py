"""Competitive Programming — parse → pick_algorithm → solve → brute → stress_test ⇄ fix → complexity → report.

Solutions are stress-tested against a brute force on random small inputs inside the runner (run_python).
"""
from __future__ import annotations

import json
import re

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_FIX = 3

STRESS_HARNESS = '''
import random, io, sys, contextlib

SOLUTION = {solution!r}
BRUTE = {brute!r}
GEN = {gen!r}


def _run_prog(src, inp):
    out = io.StringIO()
    g = {{"__name__": "__main__", "input": iter(inp.splitlines()).__next__}}
    sys.stdin = io.StringIO(inp)
    with contextlib.redirect_stdout(out):
        exec(src, g)
    return out.getvalue().split()


def run(input):
    random.seed(input.get("seed", 1))
    gen_ns = {{}}
    exec(GEN, gen_ns)
    for i in range(int(input.get("trials", 150))):
        case = gen_ns["gen"](random)
        a = _run_prog(SOLUTION, case)
        b = _run_prog(BRUTE, case)
        if a != b:
            return {{"ok": False, "case": case, "solution": a[:50], "brute": b[:50], "trial": i}}
    return {{"ok": True, "trials": int(input.get("trials", 150))}}
'''


class CPState(c.BaseState, total=False):
    problem: dict
    approach: dict
    solution: str
    brute: str
    gen: str
    stress: dict
    fixes: int
    max_fix: int
    complexity: str


def parse_problem(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("parse_problem", "Parsing the problem statement")
    text = c.instruction(state)
    out = c.ask_json(ctx, "Extract the problem. title = its full NAME (e.g. 'Valid Parentheses', not just a number); "
                          "number = its site number if any; url = its page URL if known. Return {\"title\", "
                          "\"number\", \"url\", \"input_format\", \"output_format\", "
                          "\"constraints\", \"samples\": [{\"input\", \"output\"}]}.", text,
                     fallback=lambda: {"title": text[:80], "input_format": "", "output_format": "", "constraints": "",
                                       "samples": []},
                     validate=lambda d: "title" in d, role="coder")
    if not out.get("url") and "leetcode" in text.lower() and re.search(r"[A-Za-z]", str(out.get("title", ""))):
        out["url"] = "https://leetcode.com/problems/" + re.sub(r"[^a-z0-9]+", "-", str(out["title"]).lower()).strip("-") + "/"
    return {"problem": out, "fixes": 0,
            "max_fix": max(1, min(MAX_FIX, int(c.budget(ctx).get("fix_attempts", MAX_FIX))))}


def pick_algorithm(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("pick_algorithm", "Choosing an algorithm")
    t = c.instruction(state).lower()
    guess = ("dynamic programming" if any(w in t for w in ("ways", "maximum sum", "minimum cost", "subsequence")) else
             "graph search (BFS/DFS)" if any(w in t for w in ("graph", "edges", "path", "grid")) else
             "binary search" if any(w in t for w in ("minimum possible", "maximum possible", "at least")) else
             "greedy / sorting")
    out = c.ask_json(ctx, "Pick the algorithm and justify it against the constraints. Return {\"algorithm\", \"idea\", "
                          "\"complexity\"}.", json.dumps(state["problem"]),
                     fallback=lambda: {"algorithm": guess, "idea": "Derived from problem keywords (offline mode).",
                                       "complexity": "unknown"},
                     validate=lambda d: bool(d.get("algorithm")), role="coder")
    c.emit_plan(ctx, out, "Approach")
    return {"approach": out}


def solve(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("solve", "Writing the solution" + (f" (fix {state['fixes']})" if state.get("fixes") else ""))
    if c.is_mock(ctx):
        return {"solution": ""}
    prev = state.get("stress") or {}
    txt = c.ask_text(ctx, "Write an efficient Python 3 solution reading stdin and writing stdout. One ```python block.",
                     f"Problem: {json.dumps(state['problem'])}\nApproach: {state['approach']}\n"
                     f"Previous solution:\n{state.get('solution', '')}\nCounterexample: {json.dumps(prev) if prev and not prev.get('ok') else ''}",
                     fallback=lambda: "", role="coder", trim=False, min_tokens=2000)
    b = c.code_blocks(txt)
    return {"solution": b[0] if b else ""}


def brute(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("brute", "Writing brute force + random test generator")
    if c.is_mock(ctx) or not state.get("solution") or state.get("brute"):
        return {}
    txt = c.ask_text(ctx, ("Write (1) an obviously-correct brute force Python program for TINY inputs (stdin/stdout) and "
                           "(2) `def gen(random) -> str` producing a random tiny valid input. Two ```python blocks in that order."),
                     json.dumps(state["problem"]), fallback=lambda: "", role="coder", trim=False, min_tokens=2000)
    b = c.code_blocks(txt)
    return {"brute": b[0] if len(b) > 0 else "", "gen": b[1] if len(b) > 1 else ""}


def stress_test(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("stress_test", "Stress-testing against brute force in the sandbox")
    if not (state.get("solution") and state.get("brute") and state.get("gen")):
        return {"stress": {"ok": None, "skipped": True}}
    code = STRESS_HARNESS.format(solution=state["solution"], brute=state["brute"], gen=state["gen"])
    r = c.tool(ctx, "run_python", code=code, input={"trials": 150}, timeout=90)
    res = r.get("output") if c.ok(r) and isinstance(r.get("output"), dict) else {"ok": False, "error": c.short(r, 800)}
    ctx.emit("Stress test passed" if res.get("ok") else f"Counterexample found: {c.short(res, 200)}",
             status="done" if res.get("ok") else "failed")
    return {"stress": res, "fixes": state.get("fixes", 0) + (0 if res.get("ok") else 1)}


def complexity(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("complexity", "Checking complexity vs constraints")
    out = c.ask_text(ctx, "State time and memory complexity of the solution and whether it fits the constraints. Two lines.",
                     f"Constraints: {state['problem'].get('constraints')}\nSolution:\n{state.get('solution', '')}",
                     fallback=lambda: f"Expected: {state['approach'].get('complexity', 'unknown')}", role="coder")
    return {"complexity": out}


def _judge_format(ctx, state: CPState) -> str:
    """Function-signature judges (LeetCode) never read stdin: convert the stress-tested stdin program into the
    site's `class Solution` method (T1 pasted stdin code → guaranteed Wrong Answer). '' when not needed/possible."""
    where = f"{c.instruction(state)} {state['problem'].get('url', '')}"
    if not state.get("solution") or not re.search(r"(?i)leetcode", where) or c.is_mock(ctx):
        return ""
    txt = c.ask_text(ctx, "Convert this verified Python solution into LeetCode's exact format: `class Solution` with "
                          "the problem's official method name and signature (typing imports if needed). Same algorithm, "
                          "no stdin/stdout, no main, no prints. One ```python block.",
                     f"Problem: {state['problem'].get('title', '')} {state['problem'].get('url', '')}\n"
                     f"Verified program:\n{state['solution']}", fallback=lambda: "", role="coder", trim=False,
                     min_tokens=1500)
    b = c.code_blocks(txt)
    code = b[0] if b else ""
    ok = re.search(r"\bclass\s+Solution\b", code) and not re.search(r"sys\.stdin|\binput\s*\(", code)
    if code and ok:
        ctx.emit("Converted the solution to LeetCode's class Solution format")
    return code if ok else ""


def finalize(state: CPState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    st = state.get("stress", {})
    a = state["approach"]
    pr = state["problem"]
    # WHICH problem comes first: the next step (e.g. the browser step that submits it) must know it — T1's submit
    # step only saw "single-pass stack matching — 20" and went to the wrong problem
    ident = (f"Problem: {pr.get('title', '')}" + (f" (#{pr['number']})" if pr.get("number") else "")
             + (f" — {pr['url']}" if pr.get("url") else ""))
    md = [f"**{ident}**", "", f"**Algorithm:** {a.get('algorithm')}", "", a.get("idea", ""), "",
          f"**Complexity:** {state.get('complexity')}"]
    submit_code = _judge_format(ctx, state)
    if submit_code:   # the ONLY code block = what the site accepts (the stdin version stays in the artifact)
        md += ["", "Code to submit (LeetCode `class Solution` format, same logic as the stress-tested program):",
               "```python", submit_code, "```"]
    elif state.get("solution"):
        md += ["", "```python", state["solution"], "```"]
    md += ["", "**Stress test:** " + ("passed ✓" if st.get("ok") else "skipped" if st.get("skipped") or st.get("ok") is None
                                      else f"failed — counterexample `{st.get('case')}`")]
    status = "success" if st.get("ok") or (st.get("skipped") and state.get("solution")) else "partial"
    return {"report": ctx.report(f"{a.get('algorithm')} — {state['problem'].get('title', '')[:100]}", result="\n".join(md),
                                 status=status,
                                 artifacts=[{"kind": "file", "title": "solution.py", "content": state.get("solution", "")}]
                                 if state.get("solution") else [])}


def after_stress(state: CPState) -> str:
    st = state.get("stress", {})
    if st.get("ok") is False and state.get("fixes", 0) < state.get("max_fix", MAX_FIX):
        return "solve"
    return "complexity"


@archetype("competitive", "Competitive programmer: parse → algorithm → solve → brute+generator → stress test ⇄ fix → complexity")
def build(spec):
    g = StateGraph(CPState)
    for n, fn in [("parse_problem", parse_problem), ("pick_algorithm", pick_algorithm), ("solve", solve),
                  ("brute", brute), ("stress_test", stress_test), ("complexity", complexity), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "parse_problem")
    g.add_edge("parse_problem", "pick_algorithm")
    g.add_edge("pick_algorithm", "solve")
    g.add_edge("solve", "brute")
    g.add_edge("brute", "stress_test")
    g.add_conditional_edges("stress_test", after_stress, ["solve", "complexity"])
    g.add_edge("complexity", "finalize")
    g.add_edge("finalize", END)
    return g
