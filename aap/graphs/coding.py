"""Coding Agent — Codex/Muse-style autonomous software engineer.

intake → repo_map → explore → plan → reproduce → implement → verify ─ok─→ self_review ─ok─→ finalize
                                          ▲                │fail                │issues (once)
                                          │                ▼                    │
                                          └──────────── diagnose ◄──────────────┘ (→ implement)
                                                       (escalates to debugging_agent on the last attempt)

* Works on a repo under /workspace, /shared or /mounts/*; commits to an `agentos/<slug>` branch when it is a git repo.
* All execution happens through runner tools (sandboxed). Every node has a deterministic mock-mode path.
"""
from __future__ import annotations

import json
import re
import uuid

from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c

MAX_FIX_ROUNDS = 3
MAX_REVIEW_LOOPS = 1
KEEP_TOOL_OUTPUTS = 8          # older tool outputs are trimmed from the ReAct context
REACT_TOOLS = ["fs_read", "fs_write", "fs_patch", "fs_list", "search_code", "run_command", "git_diff", "web_fetch"]
MANIFESTS = ["package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Makefile", "Cargo.toml",
             "go.mod", "build.gradle", "build.gradle.kts", "settings.gradle", "CMakeLists.txt", "pom.xml", "README.md",
             "tsconfig.json", "vite.config.ts", "vite.config.js", "pytest.ini", "tox.ini", ".eslintrc.json"]
BUGFIX_RE = re.compile(r"\b(fix|bug|error|crash|fails?|failing|broken|exception|regression|doesn'?t work|wrong)\b", re.I)
SECRET_RE = re.compile(r"(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}|sk-[A-Za-z0-9]{20,}", re.I)
DEBUG_RE = re.compile(r"^\+\s*(print\(|console\.log\(|debugger;|import pdb|breakpoint\(\)|System\.out\.println)")
ERR_LOC_RES = [re.compile(r'File "([^"]+)", line (\d+)'),                       # python traceback
               re.compile(r"((?:/[\w.\-]+)+\.\w+):(\d+)(?::\d+)?"),             # path:line[:col]
               re.compile(r"at .*?\(((?:/[\w.\-]+)+\.\w+):(\d+):\d+\)")]         # js stack


class CodingState(c.BaseState, total=False):
    repo: str
    is_bugfix: bool
    criteria: list
    repo_map: dict
    is_repo: bool
    relevant: dict            # path -> content excerpt
    assumptions: list
    todo: list                # [{id, title, files, done}]
    baseline: dict
    test_cmd: str
    changed: list
    commands: list            # [{cmd, phase, exit_code, ok}]
    impl_notes: list
    verify: dict
    attempts: int
    fix_hint: str
    escalated: bool
    review: dict
    review_rounds: int
    diff: str
    branch: str
    max_fix: int
    max_review: int
    react_steps: int


# ============================================================================================ helpers
def _abs(repo: str, f: str) -> str:
    return f if f.startswith("/") else f"{repo.rstrip('/')}/{f.removeprefix('./')}"


def _run(ctx, state: dict, cmd: str, phase: str, timeout: int = 300) -> dict:
    r = c.tool(ctx, "run_command", cmd=cmd, cwd=state["repo"], timeout=timeout)
    if not c.ok(r):
        rec = {"cmd": cmd, "phase": phase, "exit_code": None, "ok": False, "output": str(r.get("error", ""))[-4000:]}
    else:
        out = (str(r.get("stdout", "")) + "\n" + str(r.get("stderr", ""))).strip()
        rec = {"cmd": cmd, "phase": phase, "exit_code": r.get("exit_code"), "ok": r.get("exit_code") == 0,
               "timed_out": r.get("timed_out", False), "output": out[-6000:]}
    return rec


DEV_TOOLS = {"pytest": "pytest", "ruff": "ruff", "flake8": "flake8", "mypy": "mypy", "black": "black",
             "pylint": "pylint", "coverage": "coverage", "hypothesis": "hypothesis", "pytest_cov": "pytest-cov",
             "isort": "isort"}
_NO_MODULE = re.compile(r"No module named '?([A-Za-z_][\w]*)")
_NO_CMD = re.compile(r"(?:^|\s)([\w.-]+): (?:command )?not found|'([\w.-]+)' is not recognized")


def env_problem(output: str) -> tuple[str, str] | None:
    """A verification failure caused by the ENVIRONMENT, not the code: ('python3', '') when `python` is missing,
    ('install', pkg) for a missing dev tool. Project modules ('No module named calc') are real bugs → None."""
    for m in _NO_CMD.finditer(output or ""):
        name = m.group(1) or m.group(2)
        if name == "python":
            return ("python3", "")
        if name in DEV_TOOLS:
            return ("install", DEV_TOOLS[name])
    m = _NO_MODULE.search(output or "")
    if m and m.group(1) in DEV_TOOLS:
        return ("install", DEV_TOOLS[m.group(1)])
    return None


def _run_repairing(ctx, state: dict, cmd: str, phase: str, tried: set) -> dict:
    """Run a verification command; if it failed only because the environment lacks python/a dev tool, repair that
    (each problem once, at most 2 in a row) and re-run — 16.11: 'pytest' missing in the VM made agents debug
    correct code for 200k tokens."""
    rec = _run(ctx, state, cmd, phase)
    for _ in range(2):
        fix = None if rec["ok"] else env_problem(rec["output"])
        if not fix or fix in tried:
            break
        tried.add(fix)
        if fix[0] == "python3":
            ctx.emit("`python` is not on this machine — re-running with `python3` (environment, not a code bug)")
            cmd = re.sub(r"(?<![\w/.-])python(?=\s)", "python3", cmd)
        else:
            ctx.emit(f"`{fix[1]}` is missing in this environment — installing it and re-running (environment, not a "
                     "code bug)")
            _run(ctx, state, f"python3 -m pip install -q {fix[1]} || python3 -m pip install -q "
                             f"--break-system-packages {fix[1]} || python -m pip install -q {fix[1]}", "setup")
        rec = _run(ctx, state, cmd, phase)
    return rec


def _emit_todo(ctx, todo: list, msg: str = "Todo updated") -> None:
    c.emit_plan(ctx, {"todo": todo, "done": sum(1 for t in todo if t.get("done")), "total": len(todo)}, msg)


def detect_repo(files: dict[str, str], paths: list[str]) -> dict:
    """Pure function: stack/framework/commands from manifest contents + file list."""
    names = {p.rsplit("/", 1)[-1] for p in paths}
    m = {"stack": "unknown", "framework": "", "commands": {"test": "", "build": "", "lint": "", "install": ""},
         "languages": {}}
    for p in paths:
        ext = p.rsplit(".", 1)[-1] if "." in p.rsplit("/", 1)[-1] else ""
        if ext:
            m["languages"][ext] = m["languages"].get(ext, 0) + 1
    pkg = files.get("package.json")
    if pkg is not None:
        try:
            pj = json.loads(pkg)
        except Exception:  # noqa: BLE001
            pj = {}
        deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
        scripts = pj.get("scripts", {})
        m["stack"] = "typescript" if "typescript" in deps or "tsconfig.json" in names else "javascript"
        m["framework"] = next((f for f in ("next", "react", "vue", "svelte", "express", "vite") if f in deps), "")
        runner = "pnpm" if "pnpm-lock.yaml" in names else "yarn" if "yarn.lock" in names else "npm"
        m["commands"]["install"] = f"{runner} install"
        if "test" in scripts and "no test specified" not in scripts["test"]:
            m["commands"]["test"] = f"{runner} test --silent" if runner == "npm" else f"{runner} test"
        if "build" in scripts:
            m["commands"]["build"] = f"{runner} run build"
        if "lint" in scripts:
            m["commands"]["lint"] = f"{runner} run lint"
        elif "typecheck" in scripts:
            m["commands"]["lint"] = f"{runner} run typecheck"
    elif {"build.gradle", "build.gradle.kts", "settings.gradle"} & names:
        m.update(stack="kotlin" if any(p.endswith(".kt") for p in paths) else "java", framework="android/gradle")
        m["commands"].update(test="./gradlew test", build="./gradlew assembleDebug", lint="./gradlew lint")
    elif "Cargo.toml" in names:
        m.update(stack="rust")
        m["commands"].update(test="cargo test", build="cargo build", lint="cargo clippy")
    elif "go.mod" in names:
        m.update(stack="go")
        m["commands"].update(test="go test ./...", build="go build ./...", lint="go vet ./...")
    elif "CMakeLists.txt" in names or any(p.endswith((".cpp", ".cc", ".hpp")) for p in paths):
        m.update(stack="cpp", framework="cmake" if "CMakeLists.txt" in names else "")
        if "CMakeLists.txt" in names:
            m["commands"].update(build="cmake -S . -B build && cmake --build build", test="ctest --test-dir build")
    elif any(p.endswith(".py") for p in paths) or {"pyproject.toml", "requirements.txt", "setup.py"} & names:
        m["stack"] = "python"
        py = (files.get("pyproject.toml", "") + files.get("requirements.txt", "") + files.get("setup.cfg", "")).lower()
        m["framework"] = next((f for f in ("django", "fastapi", "flask", "langgraph", "pandas") if f in py), "")
        has_tests = any(re.search(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$)", p) for p in paths)
        if has_tests or "pytest" in py or "pytest.ini" in names:
            m["commands"]["test"] = "python -m pytest -q"
        if "ruff" in py:
            m["commands"]["lint"] = "ruff check ."
        elif "flake8" in py:
            m["commands"]["lint"] = "flake8"
        if "requirements.txt" in names:
            m["commands"]["install"] = "pip install -r requirements.txt"
    mk = files.get("Makefile", "")
    for target in ("test", "build", "lint"):
        if re.search(rf"^{target}\s*:", mk, re.M) and not m["commands"][target]:
            m["commands"][target] = f"make {target}"
    return m


def error_locations(output: str, repo: str) -> list[tuple[str, int]]:
    locs, seen = [], set()
    for rx in ERR_LOC_RES:
        for f, line in rx.findall(output):
            if "site-packages" in f or "node_modules" in f or f.startswith("<"):
                continue
            key = (f, int(line))
            if key not in seen and (f.startswith(repo) or not f.startswith("/")):
                seen.add(key)
                locs.append(key)
    return locs[-5:]


def diff_stats(diff: str) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    cur = None
    for line in diff.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            cur = m.group(1)
            stats.setdefault(cur, {"added": 0, "removed": 0})
        elif cur and line.startswith("+") and not line.startswith("+++"):
            stats[cur]["added"] += 1
        elif cur and line.startswith("-") and not line.startswith("---"):
            stats[cur]["removed"] += 1
    return stats


# ============================================================================================ nodes
def intake(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("intake", "Understanding the coding task")
    text = c.instruction(state)
    repo = ctx.spec.config.get("repo") or ""
    m = re.search(r"(/(?:workspace|mounts|shared)(?:/[\w.\-]+)*)", text)
    if m and not repo:
        repo = m.group(1)
        if "." in repo.rsplit("/", 1)[-1] and c.FILE_RE.fullmatch(repo.rsplit("/", 1)[-1]):
            repo = repo.rsplit("/", 1)[0] or "/workspace"
    repo = repo or "/workspace"
    is_bugfix = bool(BUGFIX_RE.search(text))
    b = c.budget(ctx)
    crit = c.ask_json(ctx, "Extract concrete, checkable acceptance criteria for this coding task. "
                           "Return {\"criteria\": [..]}.",
                      f"Task:\n{text}\n\nContext:\n{c.context_text(state)}",
                      fallback=lambda: {"criteria": [f"Task done: {text[:160]}", "Existing tests still pass",
                                                     "No unrelated changes"]},
                      validate=lambda d: isinstance(d.get("criteria"), list) and d["criteria"], role="coder")
    return {"repo": repo, "is_bugfix": is_bugfix, "criteria": [str(x) for x in crit["criteria"]][:8], "attempts": 0,
            "review_rounds": 0, "changed": [], "commands": [], "impl_notes": [], "escalated": False,
            "max_fix": max(1, int(b.get("fix_attempts", MAX_FIX_ROUNDS))),
            "max_review": int(b.get("review_loops", MAX_REVIEW_LOOPS)),
            "react_steps": int(ctx.spec.config.get("max_steps") or max(12, int(b.get("react_steps", 30))))}


def repo_map(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    repo = state["repo"]
    ctx.node("repo_map", f"Mapping repository {repo}")
    listing = c.tool(ctx, "fs_list", path=repo, depth=4)
    entries = listing.get("entries", []) if c.ok(listing) else []
    skip = ("/node_modules/", "/.git/", "/dist/", "/build/", "/__pycache__/", "/.venv/", "/venv/")
    paths = [e["path"] for e in entries if not any(s in e["path"] + "/" for s in skip)]
    files = [p for p, e in zip(paths, entries) if e.get("type", "file") == "file"] or paths
    manifest_text = {}
    depth0 = repo.rstrip("/").count("/") + 1
    for p in files:
        name = p.rsplit("/", 1)[-1]
        if name in MANIFESTS and p.count("/") <= depth0 + 1 and name not in manifest_text:
            r = c.tool(ctx, "fs_read", path=p)
            if c.ok(r):
                manifest_text[name] = str(r.get("content", ""))[:6000]
    rm = detect_repo(manifest_text, files)
    for k in ("test", "build", "lint"):
        if ctx.spec.config.get(f"{k}_cmd"):
            rm["commands"][k] = ctx.spec.config[f"{k}_cmd"]
    rm["stack"] = rm["stack"] if rm["stack"] != "unknown" else ctx.spec.config.get("stack", "unknown")
    dirs = sorted({p[len(repo):].lstrip("/").split("/")[0] for p in files if "/" in p[len(repo):].lstrip("/")})
    rm.update(file_count=len(files), top_dirs=dirs[:30], key_files=sorted(manifest_text)[:15],
              tree=[p[len(repo):].lstrip("/") for p in files][:300])
    summary = (f"Repo {repo}: stack={rm['stack']} framework={rm['framework'] or '-'} files={len(files)} "
               f"dirs={','.join(dirs[:12])} test=`{rm['commands']['test'] or '-'}` build=`{rm['commands']['build'] or '-'}` "
               f"lint=`{rm['commands']['lint'] or '-'}`")
    ctx.emit(summary)
    if c.can(ctx, "memory_remember") and files:
        c.tool(ctx, "memory_remember", text=summary, kind="project", tags=["repo_map", repo])
    return {"repo_map": rm, "test_cmd": rm["commands"]["test"], "relevant": {f"manifest:{k}": v[:2500]
                                                                               for k, v in manifest_text.items()}}


def explore(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("explore", "Exploring relevant code")
    repo, text = state["repo"], c.instruction(state)
    rel = dict(state.get("relevant", {}))
    git = c.tool(ctx, "git_status", repo=repo)
    is_repo = c.ok(git) and git.get("exit_code", 0) == 0 and "not a git repository" not in str(git).lower()
    for f in c.files_in(text)[:6]:
        r = c.tool(ctx, "fs_read", path=_abs(repo, f))
        if c.ok(r):
            rel[_abs(repo, f)] = str(r.get("content", ""))[:8000]
    hits: dict[str, int] = {}
    if c.can(ctx, "search_code"):
        terms = [w for w in re.findall(r"`([^`]{3,40})`", text)] + \
                [w for w in c.keywords(text, 8) if len(w) > 4][:3]
        for t in terms[:4]:
            r = c.tool(ctx, "search_code", pattern=re.escape(t), path=repo)
            for mt in (r.get("matches", []) if c.ok(r) else [])[:30]:
                hits[mt["path"]] = hits.get(mt["path"], 0) + 1
    for p, _ in sorted(hits.items(), key=lambda x: -x[1])[:4]:
        if p not in rel:
            r = c.tool(ctx, "fs_read", path=p)
            if c.ok(r):
                rel[p] = str(r.get("content", ""))[:6000]
    ctx.emit(f"Explored {len(rel)} relevant file(s); git repo: {bool(is_repo)}")
    return {"relevant": rel, "is_repo": bool(is_repo)}


def plan(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("plan", "Planning the change")
    text, rm = c.instruction(state), state["repo_map"]
    mentioned = c.files_in(text)[:5]
    fb_todo = ([{"title": "Reproduce the bug with a failing test", "files": []}] if state.get("is_bugfix") else []) + \
        ([{"title": f"Edit {f}", "files": [_abs(state['repo'], f)]} for f in mentioned] or
         [{"title": f"Implement: {text[:120]}", "files": []}]) + \
        [{"title": "Run tests/build and fix failures", "files": []}]
    out = c.ask_json(ctx, (
        "Plan the change as an ordered todo list. Each item: {\"title\", \"files\": [paths to create/edit]}. "
        "Also list any assumptions you make where the task is ambiguous (do not ask questions; decide and record). "
        "Return {\"todo\": [...], \"assumptions\": [...]}."),
        f"Task:\n{text}\nCriteria: {state['criteria']}\nRepo map: {json.dumps({k: rm[k] for k in ('stack', 'framework', 'commands', 'top_dirs', 'key_files')})}\n"
        f"Files:\n" + "\n".join(rm.get("tree", [])[:200]) + "\n\nRelevant code:\n" +
        "\n\n".join(f"--- {p}\n{s[:3000]}" for p, s in list(state.get("relevant", {}).items())[:6]),
        fallback=lambda: {"todo": fb_todo, "assumptions": []},
        validate=lambda d: isinstance(d.get("todo"), list) and d["todo"], role="coder")
    todo = []
    for i, t in enumerate(out["todo"][:12]):
        t = t if isinstance(t, dict) else {"title": str(t)}
        todo.append({"id": f"t{i + 1}", "title": str(t.get("title", ""))[:200],
                     "files": [_abs(state["repo"], f) for f in t.get("files", []) if isinstance(f, str)][:6],
                     "done": False})
    assumptions = [str(a) for a in out.get("assumptions", [])][:6]
    c.emit_plan(ctx, {"todo": todo, "assumptions": assumptions, "repo": state["repo"], "stack": rm["stack"],
                      "commands": rm["commands"]}, "Coding plan")
    return {"todo": todo, "assumptions": assumptions}


def reproduce(state: CodingState, config) -> dict:
    """Test-first for bug fixes: capture a failing baseline (writing a reproduction test when none exists)."""
    ctx = get_ctx(config)
    if not state.get("is_bugfix"):
        ctx.node("reproduce", "Feature task — capturing baseline")
        if state.get("test_cmd"):
            base = _run(ctx, state, state["test_cmd"], "baseline")
            return {"baseline": base,
                    "commands": state["commands"] + [{k: base[k] for k in ("cmd", "phase", "exit_code", "ok")}]}
        return {"baseline": {}}
    ctx.node("reproduce", "Reproducing the bug (test-first)")
    cmds, test_cmd, created = list(state["commands"]), state.get("test_cmd", ""), []
    if not test_cmd and state["repo_map"]["stack"] == "python" and not c.is_mock(ctx):
        txt = c.ask_text(ctx, ("Write a minimal pytest test that reproduces the bug described (it must FAIL before the fix "
                               "and pass after). Import the module under test by file name. One ```python block."),
                         f"Bug: {c.instruction(state)}\nRelevant code:\n" +
                         "\n\n".join(f"--- {p}\n{s[:4000]}" for p, s in list(state.get("relevant", {}).items())[:4]),
                         fallback=lambda: "", role="coder", min_tokens=1500, soul=False)
        blocks = c.code_blocks(txt)
        if blocks:
            path = f"{state['repo']}/tests/test_repro_{c.slugify(c.instruction(state), 24)}.py"
            if c.ok(c.tool(ctx, "fs_write", path=path, content=blocks[0])):
                created.append(path)
                test_cmd = f"python -m pytest -q {path}"
    base = _run(ctx, {**state, "commands": cmds}, test_cmd, "reproduce") if test_cmd else {}
    if base:
        cmds.append({k: base[k] for k in ("cmd", "phase", "exit_code", "ok")})
        ctx.emit("Bug reproduced (tests fail)" if not base["ok"] else "Baseline tests pass — bug not covered by tests",
                 status="info")
    return {"baseline": base, "test_cmd": test_cmd, "commands": cmds,
            "changed": sorted(set(state.get("changed", []) + created))}


def _mock_implement(ctx, state: CodingState) -> tuple[list, list]:
    text = c.instruction(state)
    changed, notes = [], []
    blocks = c.code_blocks(text)
    for i, f in enumerate(c.files_in(text)[:3]):
        path = _abs(state["repo"], f)
        body = blocks[i] if i < len(blocks) else None
        if body is None:
            if c.ok(c.tool(ctx, "fs_read", path=path)):
                notes.append(f"{path} exists; mock mode does not rewrite existing files")
                continue
            cm = "#" if path.endswith((".py", ".sh", ".yaml", ".yml", ".toml")) else "//"
            body = f"{cm} Created by {ctx.spec.name}\n{cm} Task: {text[:200]}\n"
        if c.ok(c.tool(ctx, "fs_write", path=path, content=body)):
            changed.append(path)
            notes.append(f"wrote {path}")
    return changed, notes


def implement(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    rnd = state.get("attempts", 0) + state.get("review_rounds", 0)
    ctx.node("implement", "Implementing" if rnd == 0 else f"Applying fixes (round {rnd})")
    todo = [dict(t) for t in state["todo"]]
    changed = list(state.get("changed", []))
    if c.is_mock(ctx):
        ch, notes = _mock_implement(ctx, state) if rnd == 0 else ([], [])
        changed = sorted(set(changed + ch))
        for t in todo:
            if not t["files"] or set(t["files"]) & set(changed) or "test" in t["title"].lower():
                t["done"] = True
        _emit_todo(ctx, todo)
        return {"changed": changed, "todo": todo, "impl_notes": state.get("impl_notes", []) + notes}

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    names = [n for n in REACT_TOOLS if c.can(ctx, n)]
    done_ids: set[str] = {t["id"] for t in todo if t["done"]}

    def mark_todo_done(todo_id: str, note: str = "") -> str:
        done_ids.add(todo_id)
        for t in todo:
            if t["id"] == todo_id:
                t["done"] = True
        _emit_todo(ctx, todo, f"Done: {todo_id} {note}"[:120])
        return "ok"

    progress_tool = StructuredTool.from_function(
        func=mark_todo_done, name="mark_todo_done",
        description="Mark a todo item as completed once its changes are made. Args: todo_id, note.")
    model = ctx.role_model("coder").bind_tools(ctx.lc_tools(names) + [progress_tool])
    rm = state["repo_map"]
    sys = c.system(ctx, (
        "You are an autonomous senior software engineer working in a sandboxed container (Codex-style). "
        "Loop: read the relevant code, make precise edits (fs_patch for small edits with exact old text; fs_write for new "
        "files), run commands to check your work, and call mark_todo_done after finishing each todo item. "
        "Keep changes minimal and focused; follow the project's existing conventions; never add secrets or debug prints. "
        "Do not ask the user questions — decide, and note assumptions. When everything is done, reply with a short "
        "summary and NO tool calls."))
    user = (f"Repository: {state['repo']}\nStack: {rm['stack']} {rm['framework']}\nCommands: {rm['commands']}\n"
            f"Task: {c.instruction(state)}\nAcceptance criteria: {state['criteria']}\n"
            f"Assumptions: {state.get('assumptions')}\nTodo: {json.dumps(todo)}\n"
            f"Files:\n" + "\n".join(rm.get("tree", [])[:250]) +
            "\n\nRelevant code:\n" + "\n\n".join(f"--- {p}\n{s[:3500]}" for p, s in list(state.get("relevant", {}).items())[:6]) +
            (f"\n\nBaseline test output:\n{state['baseline'].get('output', '')[-2000:]}" if state.get("baseline") else "") +
            (f"\n\nPREVIOUS VERIFICATION FAILED. Diagnosis:\n{state['fix_hint']}" if state.get("fix_hint") else "") +
            (f"\n\nSELF-REVIEW ISSUES TO FIX: {state['review'].get('issues')}"
             if state.get("review") and not state["review"].get("complete") else ""))
    msgs: list = [SystemMessage(sys), HumanMessage(user)]
    notes = list(state.get("impl_notes", []))
    budget = int(state.get("react_steps") or 30)
    final_text, steps = "", 0
    from ..core import supervisor
    sup_hist: list = []
    while steps < budget:
        steps += 1
        ctx.run.check_cancel()
        if supervisor.react_checkpoint(ctx, c.instruction(state), sup_hist, steps - 1, msgs):
            final_text = "Stopped by the Main Agent: no progress (see the supervisor note in the timeline)."
            break
        # context trimming: keep only the most recent tool outputs verbatim
        tool_idx = [i for i, m in enumerate(msgs) if isinstance(m, ToolMessage)]
        for i in tool_idx[:-KEEP_TOOL_OUTPUTS]:
            if not str(msgs[i].content).startswith("[trimmed"):
                msgs[i] = ToolMessage(content=f"[trimmed: {str(msgs[i].content)[:160]}…]", tool_call_id=msgs[i].tool_call_id)
        ai = model.invoke(msgs)
        msgs.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            final_text = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
            break
        for tc in calls:
            name, args = tc["name"], tc.get("args") or {}
            if name == "mark_todo_done":
                out = mark_todo_done(**{k: v for k, v in args.items() if k in ("todo_id", "note")})
            elif name in names:
                try:
                    out = ctx.call_tool(name, **args)
                except Exception as e:  # noqa: BLE001 — surface to the model and keep going
                    out = {"error": f"{type(e).__name__}: {e}"}
                supervisor.note(sup_hist, name, args, out)
                if name in ("fs_write", "fs_patch") and c.ok(out) and args.get("path"):
                    changed.append(args["path"])
                if name == "run_command":
                    state_cmds = state.get("commands", [])
                    state_cmds.append({"cmd": args.get("cmd"), "phase": "implement",
                                       "exit_code": out.get("exit_code") if isinstance(out, dict) else None,
                                       "ok": c.ok(out) and out.get("exit_code") == 0})
            else:
                out = {"error": f"tool {name} is not available"}
            msgs.append(ToolMessage(content=c.short(out, 6000), tool_call_id=tc["id"]))
    else:
        final_text = f"Step budget ({budget}) exhausted."
    notes.append(f"round {rnd}: {final_text[:1500]}")
    if final_text:
        ctx.emit(final_text[:400])
    return {"changed": sorted(set(changed)), "todo": todo, "impl_notes": notes, "commands": state.get("commands", [])}


def verify(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    rm = state["repo_map"]
    cmds = rm["commands"]
    test_cmd = state.get("test_cmd") or cmds.get("test")
    ctx.node("verify", "Verifying (lint/build/tests)")
    results, history = [], list(state.get("commands", []))
    changed_py = [f for f in state.get("changed", []) if f.endswith(".py")]
    # no test suite: write a minimal smoke test so we verify more than "it compiles"
    if not test_cmd and rm["stack"] == "python" and changed_py and c.can(ctx, "fs_write"):
        smoke = f"{state['repo']}/tests/test_agentos_smoke.py"
        mods = [f[len(state["repo"]):].lstrip("/") for f in changed_py if "/tests/" not in f]
        body = ("import importlib.util, pathlib\n\nROOT = pathlib.Path(__file__).resolve().parents[1]\n\n\n"
                "def _load(rel):\n    spec = importlib.util.spec_from_file_location(rel.replace('/', '_')[:-3], ROOT / rel)\n"
                "    mod = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(mod)\n    return mod\n\n\n"
                + "\n\n".join(f"def test_import_{i}():\n    assert _load({m!r}) is not None\n" for i, m in enumerate(mods)))
        if mods and c.ok(c.tool(ctx, "fs_write", path=smoke, content=body)):
            test_cmd = f"python -m pytest -q {smoke}"
            state = {**state, "changed": sorted(set(state.get("changed", []) + [smoke]))}
    if not test_cmd and changed_py:
        test_cmd = "python -m py_compile " + " ".join(changed_py)
    js = [f for f in state.get("changed", []) if f.endswith((".js", ".mjs", ".cjs"))]
    if not test_cmd and js:
        test_cmd = " && ".join(f"node --check {f}" for f in js[:10])
    plan_cmds = [(p, cmd) for p, cmd in (("lint", cmds.get("lint")), ("build", cmds.get("build")), ("test", test_cmd))
                 if cmd and (p != "lint" or ctx.spec.config.get("run_lint", True))]
    if not plan_cmds:
        ctx.emit("No verification commands available", status="info")
        return {"verify": {"ok": True, "skipped": True, "results": [], "output": "no test/build/lint command detected"},
                "changed": state.get("changed", [])}
    tried: set = set()
    for phase, cmd in plan_cmds:
        rec = _run_repairing(ctx, state, cmd, phase, tried)
        results.append(rec)
        history.append({k: rec[k] for k in ("cmd", "phase", "exit_code", "ok")})
    blocking = [r for r in results if r["phase"] != "lint" and not r["ok"]]
    okk = not blocking
    out = "\n\n".join(f"$ {r['cmd']}  (exit {r['exit_code']})\n{r['output'][-3000:]}" for r in results)
    ctx.emit(("Verification passed" if okk else f"Verification failed: {', '.join(r['phase'] for r in blocking)}"),
             status="done" if okk else "failed")
    return {"verify": {"ok": okk, "results": results, "output": out[-9000:],
                       "lint_ok": all(r["ok"] for r in results if r["phase"] == "lint")},
            "commands": history, "test_cmd": test_cmd, "changed": state.get("changed", [])}


def diagnose(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    n = state.get("attempts", 0) + 1
    k = state.get("max_fix", MAX_FIX_ROUNDS)
    ctx.node("diagnose", f"Diagnosing failure (attempt {n}/{k})")
    out = state["verify"].get("output", "")
    locs = error_locations(out, state["repo"])
    snippets = []
    for f, line in locs:
        path = _abs(state["repo"], f)
        r = c.tool(ctx, "fs_read", path=path, start=max(1, line - 12), end=line + 12)
        if c.ok(r):
            snippets.append(f"--- {path}:{line}\n{str(r.get('content', ''))[:2500]}")
    tail = "\n".join(out.strip().splitlines()[-30:])
    hint = c.ask_text(ctx, "Identify the root cause of the failure and the exact fix (file, what to change). Be concise.",
                      f"Task: {c.instruction(state)}\nChanged files: {state.get('changed')}\nFailure output:\n{out[-5000:]}\n\n"
                      f"Code at error locations:\n" + "\n\n".join(snippets),
                      fallback=lambda: f"Failure at {locs or 'unknown location'}:\n{tail}", role="coder", soul=False)
    escalated = state.get("escalated", False)
    if n >= max(1, k - 1) and not escalated and "debugging_agent" in ctx.spec.uses and not c.is_mock(ctx):
        ctx.emit("Escalating to the Debugging Agent", status="info")
        rep = c.delegate(ctx, "debugging_agent",
                         f"Diagnose (do not rewrite unrelated code) why verification fails in {state['repo']}.\n"
                         f"Task being implemented: {c.instruction(state)}\nCommand output:\n{out[-4000:]}")
        hint += f"\n\nDebugging Agent findings ({rep.get('status')}): {c.short(rep.get('result', rep.get('summary', '')), 3000)}"
        escalated = True
    return {"attempts": n, "fix_hint": hint, "escalated": escalated}


def self_review(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("self_review", "Reviewing own diff against the task")
    diff = ""
    if state.get("is_repo"):
        d = c.tool(ctx, "git_diff", repo=state["repo"])
        diff = str(d.get("stdout", d.get("output", ""))) if c.ok(d) else ""
    issues = []
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    if any(SECRET_RE.search(l) for l in added):
        issues.append("diff appears to add a secret/credential")
    dbg = [l for l in added if DEBUG_RE.match(l)]
    if dbg:
        issues.append(f"debug output added: {dbg[0][:80]}")
    planned = {f for t in state["todo"] for f in t["files"]}
    stats = diff_stats(diff)
    unrelated = [f for f in stats if planned and not any(_abs(state["repo"], f) == p for p in planned)
                 and "test" not in f.lower()]
    if unrelated and len(unrelated) > 2:
        issues.append(f"changes outside the plan: {unrelated[:5]}")
    v = state.get("verify", {})
    if not v.get("ok", True):
        issues.append("verification still failing")
    fb = {"complete": not issues and (bool(state.get("changed")) or not c.files_in(c.instruction(state))),
          "issues": issues}
    if not c.optional_pass(ctx):   # tier 0-1: automated checks only, no LLM review pass
        rev = dict(fb)
    else:
        rev = c.ask_json(ctx, ("Review this change as a strict senior reviewer. Is the task fully done per the criteria, "
                               "with no unrelated changes, secrets or debug code? Return {\"complete\": bool, \"issues\": [..]}."),
                         f"Task: {c.instruction(state)}\nCriteria: {state['criteria']}\nVerification ok: {v.get('ok')}\n"
                         f"Automated checks: {issues}\nTodo: {state['todo']}\nDiff:\n{ctx.trim(diff) if hasattr(ctx, 'trim') else diff[:14000]}",
                         fallback=lambda: fb, validate=lambda d: "complete" in d, role="coder", trim=False)
    rev["issues"] = list(dict.fromkeys([str(i) for i in rev.get("issues", [])] + issues))
    if issues:
        rev["complete"] = False
    return {"review": rev, "diff": diff,
            "review_rounds": state.get("review_rounds", 0) + (0 if rev.get("complete") else 1)}


def finalize(state: CodingState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Committing and summarising")
    v, rev = state.get("verify", {}), state.get("review", {})
    branch = ""
    if state.get("is_repo") and state.get("changed"):
        branch = f"agentos/{c.slugify(c.instruction(state), 32)}-{uuid.uuid4().hex[:4]}"
        b = c.tool(ctx, "git_branch", repo=state["repo"], name=branch)
        cm = c.tool(ctx, "git_commit", repo=state["repo"], message=f"{ctx.spec.name}: {c.instruction(state)[:72]}")
        if not (c.ok(b) and c.ok(cm) and b.get("exit_code", 0) == 0 and cm.get("exit_code", 0) == 0):
            branch = ""
    stats = diff_stats(state.get("diff", ""))
    per_file = [f"- `{f}` (+{s['added']}/−{s['removed']})" for f, s in stats.items()] or \
        [f"- `{f}`" for f in state.get("changed", [])]
    cmds = [f"- `{x['cmd']}` → {'ok' if x['ok'] else 'exit ' + str(x['exit_code'])} ({x['phase']})"
            for x in state.get("commands", [])][-12:]
    success = v.get("ok", True) and rev.get("complete", True)
    status = "success" if success else ("partial" if state.get("changed") else "failed")
    headline = c.ask_text(ctx, "One sentence: what was changed and whether it is verified.",
                          json.dumps({"task": c.instruction(state), "changed": state.get("changed"), "ok": success}),
                          fallback=lambda: (f"{'Completed' if success else 'Partially completed'}: "
                                            f"{c.instruction(state)[:120]} — {len(state.get('changed', []))} file(s) changed, "
                                            f"verification {'passed' if v.get('ok') else 'failed'}"
                                            + (" (no test command)" if v.get("skipped") else "")), role="coder")
    followups = [str(i) for i in rev.get("issues", [])][:4]
    if v.get("skipped"):
        followups.append("Add automated tests for this project so changes can be verified")
    if not v.get("lint_ok", True):
        followups.append("Fix lint warnings reported during verification")
    md = "\n".join([f"**{headline.strip()}**", "", "### Files changed", *(per_file or ["- none"]),
                    "", "### Commands run", *(cmds or ["- none"]),
                    *(["", "### Assumptions", *[f"- {a}" for a in state.get("assumptions", [])]] if state.get("assumptions") else []),
                    "", "### Todo", *[f"- [{'x' if t['done'] else ' '}] {t['title']}" for t in state["todo"]],
                    *(["", f"Committed on branch `{branch}`."] if branch else []),
                    *(["", "### Follow-ups", *[f"- {f}" for f in followups]] if followups else [])])
    artifacts = []
    if state.get("diff"):
        artifacts.append({"kind": "diff", "title": f"Diff ({state['repo']})", "content": state["diff"][:60000]})
    if v.get("output"):
        artifacts.append({"kind": "file", "title": "Verification output", "content": v["output"][-6000:]})
    errors = [] if v.get("ok", True) else ["verification failed after fixes"]
    if not rev.get("complete", True):
        errors += [str(i) for i in rev.get("issues", [])]
    return {"branch": branch,
            "report": ctx.report(headline.strip()[:300], result=md, status=status, errors=errors, artifacts=artifacts,
                                 files_modified=state.get("changed", []), recommendations=followups)}


# ============================================================================================ routing
def after_verify(state: CodingState) -> str:
    if state["verify"].get("ok"):
        return "self_review"
    return "diagnose" if state.get("attempts", 0) < state.get("max_fix", MAX_FIX_ROUNDS) else "self_review"


def after_review(state: CodingState) -> str:
    rev = state.get("review", {})
    if rev.get("complete") or state.get("review_rounds", 0) > min(state.get("max_review", MAX_REVIEW_LOOPS), MAX_REVIEW_LOOPS) or \
            state.get("attempts", 0) >= state.get("max_fix", MAX_FIX_ROUNDS):
        return "finalize"
    return "implement"


@archetype("coding", "Codex-style engineer: repo map → explore → plan → (reproduce) → edit loop → verify ⇄ diagnose/fix → self-review → commit")
def build(spec):
    g = StateGraph(CodingState)
    for name, fn in [("intake", intake), ("repo_map", repo_map), ("explore", explore), ("plan", plan),
                     ("reproduce", reproduce), ("implement", implement), ("verify", verify), ("diagnose", diagnose),
                     ("self_review", self_review), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    for a, b in [("intake", "repo_map"), ("repo_map", "explore"), ("explore", "plan"), ("plan", "reproduce"),
                 ("reproduce", "implement"), ("implement", "verify"), ("diagnose", "implement")]:
        g.add_edge(a, b)
    g.add_conditional_edges("verify", after_verify, ["diagnose", "self_review"])
    g.add_conditional_edges("self_review", after_review, ["implement", "finalize"])
    g.add_edge("finalize", END)
    return g
