"""Graph archetype tests — offline (mock LLM), fake runtime/run context, in-memory fake tools."""
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aap_test_graphs_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aap import settings  # noqa: E402
from aap import graphs, tools  # noqa: E402,F401  (registers archetypes + tools)
from aap.agents.prebuilt.catalog import PREBUILT  # noqa: E402
from aap.core import permissions  # noqa: E402
from aap.core.sdk import ARCHETYPES, TOOLS, AgentContext, PermissionDenied  # noqa: E402
from aap.graphs.builder import SAFE_TEMPLATE, validate_code  # noqa: E402
from aap.models import AgentSpec, AgentTask, Report  # noqa: E402

settings.update({"llm_provider": "mock"})


# ------------------------------------------------------------------------------------------------ fakes
class FakeRun:
    def __init__(self):
        self.run_id = "run_test"
        self.events = []
        self.cancelled = False

    def emit(self, type, agent_id="", status="", message="", **data):
        ev = {"type": type, "agent_id": agent_id, "status": status, "message": message, "data": data}
        self.events.append(ev)
        return ev

    def check_cancel(self):
        pass

    def nodes(self, agent_id=None):
        return [e["data"].get("node") for e in self.events if e["type"] == "node"
                and (agent_id is None or e["agent_id"] == agent_id)]


class FakeRuntime:
    def __init__(self):
        self.specs = {s.id: s.model_copy(deep=True) for s in PREBUILT}
        self.registered = []
        self.delegations = []

    def get_spec(self, agent_id):
        return self.specs.get(agent_id)

    def list_specs(self, only_active=True):
        return [s for s in self.specs.values() if s.status == "active" or not only_active]

    def register(self, spec):
        self.specs[spec.id] = spec
        self.registered.append(spec)
        return spec

    def run_agent(self, task, run):
        self.delegations.append((task.agent_id, task.instruction))
        return Report(agent_id=task.agent_id, task_id=task.task_id, status="success",
                      summary=f"{task.agent_id} did it", result=f"result from {task.agent_id}",
                      recommendations=[f"rec from {task.agent_id}"])


class FakeFS:
    def __init__(self):
        self.files = {}

    def tools(self):
        fs = self.files

        def fs_list(ctx, path, depth=2):
            p = path.rstrip("/") + "/"
            return {"entries": [{"path": k, "type": "file", "size": len(v)} for k, v in sorted(fs.items())
                                if k.startswith(p)]}

        def fs_read(ctx, path, start=0, end=0):
            if path not in fs:
                return {"error": f"not found: {path}"}
            lines = fs[path].splitlines()
            if start or end:
                lines = lines[max(0, start - 1): end or None]
            return {"path": path, "content": "\n".join(lines), "lines": len(lines)}

        def fs_write(ctx, path, content):
            created = path not in fs
            fs[path] = content
            (ctx.files_created if created else ctx.files_modified).append(path)
            return {"ok": True, "created": created}

        def fs_patch(ctx, path, old, new):
            if path not in fs or old not in fs[path]:
                return {"error": "old text not found"}
            fs[path] = fs[path].replace(old, new, 1)
            return {"ok": True, "replacements": 1}

        def search_code(ctx, pattern, path="/workspace", glob=""):
            out = []
            for k, v in fs.items():
                for i, line in enumerate(v.splitlines(), 1):
                    if k.startswith(path) and re.search(pattern, line):
                        out.append({"path": k, "line": i, "text": line})
            return {"matches": out}

        def run_command(ctx, cmd, cwd="/workspace", timeout=120):
            if "pytest" in cmd and "/workspace/proj/calc.py" in fs:
                if "return a + b" in fs["/workspace/proj/calc.py"]:
                    return {"exit_code": 0, "stdout": "1 passed", "stderr": ""}
                return {"exit_code": 1, "stdout": "F\n1 failed",
                        "stderr": 'Traceback:\n  File "/workspace/proj/calc.py", line 2, in add\nAssertionError'}
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}

        def run_python(ctx, code, input=None, timeout=60):
            g = {"__name__": "sandbox"}
            try:
                exec(compile(code, "<logic>", "exec"), g)  # test-only stand-in for the runner container
                return {"ok": True, "output": g["run"](input or {}) if "run" in g else None}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        def git_status(ctx, repo):
            return {"exit_code": 128, "stdout": "", "stderr": "fatal: not a git repository"}

        def web_search(ctx, query, n=5):
            return {"results": [{"title": "Example", "url": "https://example.com/a",
                                 "snippet": "Example snippet about the topic. Second sentence."}]}

        def web_fetch(ctx, url, max_chars=20000):
            return {"url": url, "title": "Example page", "text": "The example page explains the topic. It is useful."}

        def memory(ctx, **kw):
            return {"memories": []}

        def custom_logic(ctx, input):
            code = (Path(ctx.spec.location) / "logic.py").read_text(encoding="utf-8")
            return run_python(ctx, code, input)

        def browser_open(ctx, profile="default", mode="sandbox"):
            return {"error": "browser service unavailable (test)"}

        def unavailable(ctx, **kw):
            return {"error": "service unavailable (test)"}

        return {"fs_list": fs_list, "fs_read": fs_read, "fs_write": fs_write, "fs_patch": fs_patch,
                "search_code": search_code, "run_command": run_command, "run_python": run_python,
                "git_status": git_status, "git_diff": lambda ctx, repo, staged=False: {"exit_code": 0, "stdout": ""},
                "git_log": unavailable, "git_branch": unavailable, "git_commit": unavailable,
                "web_search": web_search, "web_fetch": web_fetch, "memory_recall": memory, "memory_remember": memory,
                "custom_logic": custom_logic, "browser_open": browser_open, "browser_act": unavailable,
                "browser_snapshot": unavailable, "browser_screenshot": unavailable, "desktop_tree": unavailable,
                "desktop_act": unavailable, "desktop_screenshot": unavailable, "data_profile": unavailable,
                "data_query": unavailable, "skill_read": unavailable}


class FakeCtx(AgentContext):
    fake_tools: dict = {}
    approvals: list = []

    def call_tool(self, name, **args):
        if name not in self.spec.tools:
            raise PermissionDenied(f"{self.spec.id} may not use {name}")
        td = TOOLS.get(name)
        if td is not None:
            permissions.check(self.spec, td, args)
        out = self.fake_tools[name](self, **args)
        self.tool_log.append({"tool": name, "ok": "error" not in out if isinstance(out, dict) else True})
        return out

    def approve(self, title, detail=None):
        self.approvals.append(title)


@pytest.fixture()
def env():
    fs, rt, run = FakeFS(), FakeRuntime(), FakeRun()
    FakeCtx.fake_tools = fs.tools()
    FakeCtx.approvals = []

    def invoke(agent_id, instruction, spec=None, **task_kw):
        spec = spec or rt.get_spec(agent_id)
        task = AgentTask(task_id="t1", agent_id=spec.id, instruction=instruction, run_id=run.run_id, **task_kw)
        ctx = FakeCtx(spec=spec, task=task, run=run, runtime=rt)
        g = ARCHETYPES[spec.graph].build(spec).compile()
        out = g.invoke({"task": task.model_dump()}, config={"configurable": {"ctx": ctx, "thread_id": "t"},
                                                             "recursion_limit": 150})
        return Report.model_validate(out["report"])

    return {"fs": fs, "rt": rt, "run": run, "invoke": invoke}


# ------------------------------------------------------------------------------------------------ tests
def test_all_archetypes_registered_and_compile():
    expected = {"coding", "builder", "research", "data", "review", "debug", "testing", "browser", "desktop", "writer",
                "planner", "teacher", "competitive", "composite", "generic", "improve", "main"}
    assert expected <= set(ARCHETYPES)
    for spec in PREBUILT:
        assert spec.graph in ARCHETYPES, spec.id
        g = ARCHETYPES[spec.graph].build(spec).compile()
        assert len(g.get_graph().nodes) >= 5, spec.id


def test_catalog_tools_exist_and_ids_unique():
    ids = [s.id for s in PREBUILT]
    assert len(ids) == len(set(ids))
    assert len([s for s in PREBUILT if s.type == "prebuilt"]) >= 25
    for s in PREBUILT:
        for t in s.tools:
            assert t in TOOLS, f"{s.id}: unknown tool {t}"
        for u in s.uses:
            assert u in ids, f"{s.id}: unknown agent {u}"


def test_coding_writes_file_and_verifies(env):
    rep = env["invoke"]("coding_agent", "Create /workspace/proj/hello.py with:\n```python\nprint('hi')\n```")
    assert env["fs"].files["/workspace/proj/hello.py"] == "print('hi')"
    assert rep.status == "success", rep
    nodes = env["run"].nodes("coding_agent")
    for n in ("intake", "repo_map", "explore", "plan", "reproduce", "implement", "verify", "self_review", "finalize"):
        assert n in nodes
    assert any(e["type"] == "plan" for e in env["run"].events)


def test_coding_fix_loop_is_bounded_and_reports_failure(env):
    fs = env["fs"].files
    fs["/workspace/proj/calc.py"] = "def add(a, b):\n    return a - b\n"
    fs["/workspace/proj/tests/test_calc.py"] = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    rep = env["invoke"]("coding_agent", "Fix the bug in calc.py: add() returns the wrong result. Repo /workspace/proj")
    nodes = env["run"].nodes("coding_agent")
    assert nodes.count("diagnose") >= 1 and nodes.count("verify") >= 2
    assert rep.status in ("failed", "partial")
    assert any("verification" in e.lower() for e in rep.errors)


def test_data_analysis_ranks_weak_topics(env):
    rep = env["invoke"]("data_analysis_agent", "Analyze my progress:\narrays: 12/20\ndp: 3/15\ngraphs: 7/10")
    assert rep.status == "success"
    low = next(a for a in rep.artifacts if a["title"].startswith("lowest"))
    assert low["content"][0]["label"] == "dp"


def test_research_with_sources(env):
    rep = env["invoke"]("web_research_agent", "What does the example page say? https://example.com/page")
    assert rep.status == "success"
    assert "example" in str(rep.result).lower()


def test_writer_and_summarizer(env):
    assert env["invoke"]("writing_agent", "Write a short note about testing").status == "success"
    long = "Testing is important. " * 40
    rep = env["invoke"]("summarization_agent", long)
    assert "TL;DR" in rep.result


def test_composite_delegates_to_all_sub_agents(env):
    rep = env["invoke"]("research_agent", "Research LangGraph")
    called = [a for a, _ in env["rt"].delegations]
    assert set(called) == {"web_research_agent", "document_research_agent", "summarization_agent"}
    assert rep.status == "success"


def test_other_archetypes_run_offline(env):
    for aid, text in [("planning_agent", "Plan 2 weeks of DP practice, 2 hours per day"),
                      ("teacher_agent", "Teach me binary search"),
                      ("curriculum_agent", "Curriculum for graph algorithms"),
                      ("question_generator_agent", "Quiz on recursion"),
                      ("code_review_agent", "Review:\n```python\ndef f(x=[]):\n    try:\n        return eval(x)\n    except:\n        pass\n```"),
                      ("debugging_agent", "Fix the crash when running `python app.py`"),
                      ("testing_agent", "Write tests for /workspace/proj/calc.py"),
                      ("competitive_programming_agent", "Count the number of ways to climb n stairs"),
                      ("browser_agent", "Open https://example.com and tell me the title"),
                      ("self_improvement_agent", "Study OpenClaw and propose improvements"),
                      ("memory_agent", "What do you remember about me?")]:
        if aid == "testing_agent":
            env["fs"].files["/workspace/proj/calc.py"] = "def add(a, b):\n    return a + b\n"
        rep = env["invoke"](aid, text)
        assert rep.status in ("success", "partial", "failed"), aid
        assert rep.summary, aid
    review = env["invoke"]("code_review_agent", "Review:\n```python\ndef f(x=[]):\n    try:\n        return eval(x)\n    except:\n        pass\n```")
    issues = str(review.result)
    assert "eval" in issues and "mutable default" in issues and "bare" in issues


def test_validate_code_policy():
    assert any("import not allowed: os" in e for e in validate_code("import os\ndef run(input):\n    return {}\n"))
    assert validate_code("def run(input):\n    return eval('1')\n")
    assert validate_code("def run(input):\n    return input.__class__\n")
    assert validate_code("x = 1\n")  # missing run
    tpl = SAFE_TEMPLATE.format(name="X", purpose="p", keywords=["dp"])
    assert validate_code(tpl) == []


def test_builder_creates_tests_and_registers_agent(env):
    rep = env["invoke"]("agent_builder", "Create an agent that monitors my personal programming learning progress "
                                         "and automatically creates a weekly learning plan.")
    rt = env["rt"]
    assert rt.registered, "register() was not called"
    spec = rt.registered[-1]
    assert spec.type == "custom" and spec.created_by == "agent_builder"
    assert spec.status == "active", rep
    assert "custom_logic" in spec.tools
    loc = Path(spec.location)
    assert (loc / "logic.py").exists() and (loc / "tests.json").exists() and (loc / "prompt.md").exists()
    assert rep.status == "success"
    assert rep.result["tests"]["ok"] is True
    # smoke run executed the new agent via the runtime
    assert any(a == spec.id for a, _ in rt.delegations)
    for n in ("understand_spec", "inspect_registry", "select_components", "design", "generate_code",
              "generate_tests", "static_validation", "run_tests", "security_review", "approval_gate", "register",
              "smoke_run"):
        assert n in env["run"].nodes("agent_builder"), n


def test_builder_debug_loop_replaces_bad_code(env, monkeypatch):
    from aap.graphs import builder as b
    orig = b.generate_code

    def bad_code(state, config):
        out = orig(state, config)
        if out.get("code"):
            out["code"] = "import os\n\ndef run(input):\n    return {'summary': os.getcwd()}\n"
        return out

    monkeypatch.setattr(b, "generate_code", bad_code)
    rep = env["invoke"]("agent_builder", "Create an agent that tracks my workout progress and scores it")
    nodes = env["run"].nodes("agent_builder")
    assert "debug" in nodes
    spec = env["rt"].registered[-1]
    assert "import os" not in (Path(spec.location) / "logic.py").read_text(encoding="utf-8")
    assert spec.status == "active", rep


def test_generated_agent_runs_through_generic_graph(env):
    env["invoke"]("agent_builder", "Create an agent that analyzes my programming learning progress")
    spec = env["rt"].registered[-1]
    rep = env["invoke"](spec.id, "My progress:\narrays: 12/20\ndp: 3/15\ngraphs: 7/10", spec=spec)
    assert rep.status == "success"
    assert "dp" in str(rep.result).lower()
