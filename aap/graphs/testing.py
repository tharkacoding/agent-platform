"""Testing — discover → design_tests → write_tests → run → analyze ⇄ (repair broken tests) → report."""
from __future__ import annotations

import re

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_REPAIRS = 2


class TestingState(c.BaseState, total=False):
    repo: str
    targets: dict
    framework: str
    cases: list
    test_file: str
    test_code: str
    run: dict
    analysis: dict
    repairs: int
    max_repairs: int


def discover(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("discover", "Discovering code under test")
    text = c.instruction(state)
    m = re.search(r"(/(?:workspace|mounts|shared)(?:/[\w.\-]+)*)", text)
    repo = ctx.spec.config.get("repo") or (m.group(1) if m else "/workspace")
    if c.FILE_RE.fullmatch(repo.rsplit("/", 1)[-1]):
        repo = repo.rsplit("/", 1)[0]
    targets = {}
    for f in c.files_in(text)[:4]:
        path = f if f.startswith("/") else f"{repo}/{f}"
        r = c.tool(ctx, "fs_read", path=path)
        if c.ok(r):
            targets[path] = str(r.get("content", ""))[:12000]
    for i, b in enumerate(c.code_blocks(text)):
        targets[f"inline_{i}"] = b
    framework = "pytest" if any(p.endswith(".py") or "def " in s for p, s in targets.items()) else \
        ("jest" if any(p.endswith((".js", ".ts", ".tsx")) for p in targets) else "pytest")
    return {"repo": repo, "targets": targets, "framework": ctx.spec.config.get("framework", framework), "repairs": 0,
            "max_repairs": min(MAX_REPAIRS, int(c.budget(ctx).get("fix_attempts", MAX_REPAIRS)))}


def design_tests(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("design_tests", "Designing test cases")
    funcs = sorted({m for s in state["targets"].values() for m in re.findall(r"def (\w+)\(", s) if not m.startswith("_")})
    fb = {"cases": [{"name": f"test_{f}_exists", "target": f, "kind": "smoke"} for f in funcs[:6]] or
          [{"name": "test_placeholder", "target": "", "kind": "smoke"}]}
    out = c.ask_json(ctx, "Design test cases: happy path, edge cases, errors. Return {\"cases\": [{\"name\", \"target\", "
                          "\"kind\": unit|edge|error, \"description\"}]}.",
                     f"Task: {c.instruction(state)}\nCode:\n{c.short(state['targets'], 12000)}", fallback=lambda: fb,
                     validate=lambda d: isinstance(d.get("cases"), list) and d["cases"], role="coder")
    c.emit_plan(ctx, {"cases": out["cases"][:15]}, "Test plan")
    return {"cases": out["cases"][:15]}


def write_tests(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("write_tests", "Writing tests" + (" (repair)" if state.get("repairs") else ""))
    py_targets = [p for p in state["targets"] if p.endswith(".py")]
    module = py_targets[0].rsplit("/", 1)[-1][:-3] if py_targets else ""

    def fb() -> str:
        lines = ["import importlib", ""]
        if module:
            lines += [f"mod = importlib.import_module({module!r})", ""]
        for cs in state["cases"]:
            name = re.sub(r"\W", "_", cs.get("name", "test_case"))
            name = name if name.startswith("test") else "test_" + name
            tgt = cs.get("target")
            body = f"    assert hasattr(mod, {tgt!r})" if module and tgt else "    assert True"
            lines += [f"def {name}():", body, ""]
        return "\n".join(lines)

    txt = c.ask_text(ctx, (f"Write a complete {state['framework']} test file for these cases. Import the module under "
                           "test by its file name. Reply with one code block."),
                     f"Cases: {state['cases']}\nCode:\n{c.short(state['targets'], 12000)}\n"
                     f"Previous run output (fix broken tests, not the code): {state.get('run', {}).get('output', '')[-3000:]}",
                     fallback=fb, role="coder", min_tokens=2000)
    blocks = c.code_blocks(txt)
    code = blocks[0] if blocks else txt
    test_file = state.get("test_file") or (f"{state['repo']}/tests/test_{module or 'generated'}.py"
                                           if state["framework"] == "pytest" else f"{state['repo']}/generated.test.js")
    c.tool(ctx, "fs_write", path=test_file, content=code)   # the tool records files_created/modified
    return {"test_file": test_file, "test_code": code}


def run(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    cmd = ctx.spec.config.get("test_cmd") or (f"python -m pytest -q {state['test_file']}" if state["framework"] == "pytest"
                                               else f"npx jest {state['test_file']}")
    ctx.node("run", f"Running `{cmd}`")
    r = c.tool(ctx, "run_command", cmd=cmd, cwd=state["repo"], timeout=300)
    if not c.ok(r):
        return {"run": {"ran": False, "output": r.get("error", ""), "exit_code": None}}
    out = (str(r.get("stdout", "")) + "\n" + str(r.get("stderr", "")))[-8000:]
    return {"run": {"ran": True, "output": out, "exit_code": r.get("exit_code")}}


def analyze(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("analyze", "Analyzing results")
    out = state["run"].get("output", "")
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", out))
    failed = sum(int(x) for x in re.findall(r"(\d+) failed", out))
    errors = sum(int(x) for x in re.findall(r"(\d+) errors?", out))
    broken = bool(re.search(r"(ImportError|ModuleNotFoundError|SyntaxError|collection error)", out))
    return {"analysis": {"passed": passed, "failed": failed, "errors": errors, "tests_broken": broken,
                         "ran": state["run"].get("ran")},
            "repairs": state.get("repairs", 0) + (1 if broken else 0)}


def finalize(state: TestingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    a = state["analysis"]
    if not a["ran"]:
        summary, status = "Tests written but could not be run (no runner)", "partial"
    else:
        summary = f"{a['passed']} passed, {a['failed']} failed, {a['errors']} errors"
        status = "success" if a["failed"] == 0 and a["errors"] == 0 and not a["tests_broken"] else "partial"
    md = (f"**{summary}**\n\nTest file: `{state['test_file']}`\n\n```\n{state['run'].get('output', '')[-2500:]}\n```")
    return {"report": ctx.report(summary, result=md, status=status, files_created=[state["test_file"]],
                                 recommendations=(["Investigate failing tests — they may reveal real bugs"]
                                                  if a.get("failed") else []),
                                 artifacts=[{"kind": "file", "title": state["test_file"], "content": state["test_code"]}])}


def after_analyze(state: TestingState) -> str:
    limit = state.get("max_repairs", MAX_REPAIRS)
    return "write_tests" if state["analysis"]["tests_broken"] and state.get("repairs", 0) <= limit else "finalize"


@archetype("testing", "Tester: discover → design cases → write tests → run → analyze ⇄ repair broken tests")
def build(spec):
    g = StateGraph(TestingState)
    for n, fn in [("discover", discover), ("design_tests", design_tests), ("write_tests", write_tests), ("run", run),
                  ("analyze", analyze), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "discover")
    g.add_edge("discover", "design_tests")
    g.add_edge("design_tests", "write_tests")
    g.add_edge("write_tests", "run")
    g.add_edge("run", "analyze")
    g.add_conditional_edges("analyze", after_analyze, ["write_tests", "finalize"])
    g.add_edge("finalize", END)
    return g
