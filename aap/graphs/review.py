"""Code Review — collect → static_checks → review dimensions (loop) → rank → report."""
from __future__ import annotations

import ast
import re

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

DIMENSIONS = ["correctness", "security", "performance", "readability"]


class ReviewState(c.BaseState, total=False):
    code: dict
    static: list
    dim_index: int
    dims: list
    findings: list
    ranked: list


def _local_python_checks(path: str, src: str) -> list[dict]:
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [{"file": path, "line": e.lineno or 0, "severity": "high", "dimension": "correctness",
                 "issue": f"syntax error: {e.msg}"}]
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append({"file": path, "line": node.lineno, "severity": "medium", "dimension": "correctness",
                        "issue": "bare `except:` swallows all errors"})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            out.append({"file": path, "line": node.lineno, "severity": "high", "dimension": "security",
                        "issue": f"use of {node.func.id}()"})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > 80:
                out.append({"file": path, "line": node.lineno, "severity": "low", "dimension": "readability",
                            "issue": f"function `{node.name}` is {length} lines long"})
            for d in node.args.defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append({"file": path, "line": node.lineno, "severity": "medium", "dimension": "correctness",
                                "issue": f"mutable default argument in `{node.name}`"})
    return out


def _local_text_checks(path: str, src: str) -> list[dict]:
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{6,}", line, re.I):
            out.append({"file": path, "line": i, "severity": "high", "dimension": "security", "issue": "hard-coded secret"})
        if re.search(r"\b(TODO|FIXME|HACK)\b", line):
            out.append({"file": path, "line": i, "severity": "low", "dimension": "readability", "issue": line.strip()[:120]})
    return out


def collect(state: ReviewState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("collect", "Collecting code to review")
    code: dict = {}
    text = c.instruction(state)
    for i, b in enumerate(c.code_blocks(text)):
        code[f"snippet_{i + 1}.py" if "def " in b or "import " in b else f"snippet_{i + 1}"] = b
    for f in list((state.get("task") or {}).get("files") or []) + c.files_in(text):
        r = c.tool(ctx, "fs_read", path=f if f.startswith("/") else f"/workspace/{f}")
        if c.ok(r):
            code[f] = str(r.get("content", ""))[:40000]
    for rep in (state.get("task") or {}).get("context", []) or []:
        for a in rep.get("artifacts", []) or []:
            if a.get("kind") == "diff" and a.get("content"):
                code[f"diff from {rep.get('agent_id')}"] = str(a["content"])[:40000]
    if not code and len(text) > 40:
        code["request"] = text
    ctx.emit(f"Reviewing {len(code)} item(s)")
    lvl = c.level(ctx)
    dims = DIMENSIONS[:1] if lvl == 0 else DIMENSIONS[:2] if lvl == 1 else DIMENSIONS
    return {"code": code, "dim_index": 0, "findings": [], "dims": dims}


def static_checks(state: ReviewState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("static_checks", "Running static checks")
    found = []
    for path, src in state["code"].items():
        if path.endswith(".py") or "def " in src:
            found += _local_python_checks(path, src)
        found += _local_text_checks(path, src)
    py_files = [p for p in state["code"] if p.startswith("/") and p.endswith(".py")]
    if py_files and c.can(ctx, "run_command"):
        r = c.tool(ctx, "run_command", cmd="ruff check --output-format concise " + " ".join(py_files[:20]), timeout=60)
        if c.ok(r):
            for line in str(r.get("stdout", "")).splitlines()[:50]:
                m = re.match(r"(.+?):(\d+):\d+: (\S+) (.*)", line)
                if m:
                    found.append({"file": m.group(1), "line": int(m.group(2)), "severity": "low",
                                  "dimension": "readability", "issue": f"{m.group(3)} {m.group(4)}"})
    return {"static": found}


def review_dimension(state: ReviewState, config) -> dict:
    ctx = get_ctx(config)
    i = state.get("dim_index", 0)
    dim = state.get("dims", DIMENSIONS)[i]
    ctx.node("review_dimension", f"Reviewing {dim}")
    code_txt = "\n\n".join(f"### {p}\n{s[:12000]}" for p, s in state["code"].items())[:30000]
    out = c.ask_json(ctx, (f"Review the code ONLY for {dim}. Report real, specific problems (no style nits unless "
                           "readability). Return {\"findings\": [{\"file\", \"line\", \"severity\": high|medium|low, "
                           "\"issue\", \"fix\"}]}."),
                     f"Task: {c.instruction(state)[:1000]}\n\n{code_txt}",
                     fallback=lambda: {"findings": []}, validate=lambda d: isinstance(d.get("findings"), list), role="coder")
    new = [dict(f, dimension=dim) for f in out["findings"] if isinstance(f, dict)][:15]
    return {"findings": state.get("findings", []) + new, "dim_index": i + 1}


def rank(state: ReviewState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("rank", "Ranking findings by severity")
    order = {"high": 0, "medium": 1, "low": 2}
    allf = state.get("static", []) + state.get("findings", [])
    seen, ranked = set(), []
    for f in sorted(allf, key=lambda f: order.get(str(f.get("severity")), 3)):
        key = (f.get("file"), f.get("line"), str(f.get("issue"))[:60])
        if key not in seen:
            seen.add(key)
            ranked.append(f)
    return {"ranked": ranked[:40]}


def finalize(state: ReviewState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    r = state["ranked"]
    counts = {s: sum(1 for f in r if f.get("severity") == s) for s in ("high", "medium", "low")}
    md = [f"**{counts['high']} high, {counts['medium']} medium, {counts['low']} low** across {len(state['code'])} item(s)", ""]
    for f in r:
        md.append(f"- **[{f.get('severity')}] {f.get('dimension')}** `{f.get('file')}:{f.get('line')}` — {f.get('issue')}"
                  + (f"  \n  Fix: {f.get('fix')}" if f.get("fix") else ""))
    if not r:
        md.append("No problems found.")
    return {"report": ctx.report(md[0].replace("*", ""), result="\n".join(md),
                                 recommendations=[f"{f.get('file')}:{f.get('line')} {f.get('fix') or f.get('issue')}" for f in r[:5]],
                                 artifacts=[{"kind": "table", "title": "Findings", "content": r}])}


def next_dimension(state: ReviewState, config=None) -> str:
    return "review_dimension" if state.get("dim_index", 0) < len(state.get("dims", DIMENSIONS)) else "rank"


@archetype("review", "Code reviewer: collect → static checks → per-dimension LLM review loop → rank")
def build(spec):
    g = StateGraph(ReviewState)
    for n, fn in [("collect", collect), ("static_checks", static_checks), ("review_dimension", review_dimension),
                  ("rank", rank), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "collect")
    g.add_edge("collect", "static_checks")
    g.add_conditional_edges("static_checks", lambda s: "rank" if not s.get("code") else "review_dimension",
                            ["review_dimension", "rank"])
    g.add_conditional_edges("review_dimension", next_dimension, ["review_dimension", "rank"])
    g.add_edge("rank", "finalize")
    g.add_edge("finalize", END)
    return g
