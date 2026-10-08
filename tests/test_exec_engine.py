"""Stateful execution engine: explicit profiles, task state, handoffs, loop guard, verification, lessons, resume."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_exec_"))

from aap.core import execstate  # noqa: E402


def test_parse_profile():
    p = execstate.parse_profile
    assert p("Use Chrome profile asdf") == "asdf"
    assert p("use my Work profile and open gmail") == "Work"
    assert p('open leetcode in profile "Profile 2"') == "Profile 2"
    assert p("with profile Challa open youtube") == "Challa"
    assert p("use Profile 3") == "Profile 3"
    assert p("open 5 tabs in chrome") == ""
    assert p("open my profile page on github") == ""


def test_state_merge_keeps_user_profile_and_completed_work():
    s = execstate.new_state("submit 2 problems", profile="asdf")
    s["completed"].append({"step": "s1", "summary": "solved #1", "verified": True})
    upd = {"browser": {"profile": "other", "url": "https://leetcode.com/x", "tabs": ["a", "b"]},
           "failed": [{"action": "click submit", "error": "no effect"}],
           "completed": [{"step": "s1", "summary": "solved #1", "verified": True}]}
    m = execstate.merge(s, upd)
    assert m["browser"]["profile"] == "asdf"            # never replaced by an agent's guess
    assert m["browser"]["url"] == "https://leetcode.com/x" and len(m["browser"]["tabs"]) == 2
    assert len(m["completed"]) == 1 and len(m["failed"]) == 1   # no duplicates, nothing lost


def test_handoff_packet_has_everything():
    s = execstate.new_state("open leetcode and submit two sum", profile="asdf")
    s["success_criteria"] = ["Accepted shown"]
    s["todo"] = [{"id": "s1", "title": "solve", "status": "done", "verified": True},
                 {"id": "s2", "title": "submit", "status": "pending"}]
    s["completed"] = [{"step": "s1", "summary": "code written", "verified": True}]
    s["failed"] = [{"action": "click Submit", "error": "button disabled"}]
    s["browser"]["url"] = "https://leetcode.com/problems/two-sum/"
    h = execstate.handoff(s, {"instruction": "submit the solution"})
    for needle in ("profile=asdf", "leetcode.com/problems/two-sum", "Accepted shown", "code written [verified]",
                   "click Submit", "s2 submit (pending)", "submit the solution"):
        assert needle in h, needle


def test_lessons_roundtrip():
    execstate.record_lesson("example.com", "avoid", "clicking 'More' does nothing", strategy="use the search box")
    execstate.record_lesson("example.com", "avoid", "clicking 'More' does nothing")
    ls = execstate.lessons_for(["example.com"])
    assert len(ls) == 1 and "AVOID" in ls[0] and "search box" in ls[0]


def test_loop_guard_refuses_repeated_failing_call():
    from aap import events, registry
    from aap.core.sdk import AgentContext, ToolDef, TOOLS
    from aap.models import AgentSpec, AgentTask
    registry.init()
    calls = {"n": 0}

    def boom(ctx, x=0):
        calls["n"] += 1
        return {"error": "nope"}
    TOOLS["_t_boom"] = ToolDef("_t_boom", "", {"x": {"type": "integer"}}, boom)
    spec = AgentSpec(id="t", name="T", tools=["_t_boom"])
    run = events.create(registry.new_run("t", "x"))
    ctx = AgentContext(spec=spec, task=AgentTask(task_id="1", agent_id="t", instruction="x"), run=run, runtime=None)
    for _ in range(5):
        r = ctx.call_tool("_t_boom", x=1)
    assert calls["n"] == 2 and r.get("loop_guard")       # 3rd+ identical failing call never executes
    assert ctx.call_tool("_t_boom", x=2).get("error") == "nope"   # different args still allowed
    # observation tools legitimately repeat (one snapshot per action): never blocked while they succeed
    from aap.core import sdk as _sdk
    original = TOOLS.get("browser_snapshot")
    try:
        TOOLS["browser_snapshot"] = ToolDef("browser_snapshot", "", {}, lambda ctx: {"url": "x"})
        spec2 = AgentSpec(id="t2", name="T2", tools=["browser_snapshot"])
        ctx2 = AgentContext(spec=spec2, task=AgentTask(task_id="2", agent_id="t2", instruction="x"), run=run,
                            runtime=None)
        assert all(not ctx2.call_tool("browser_snapshot").get("loop_guard") for _ in range(30))
        assert "browser_snapshot" in _sdk._OBSERVE_TOOLS
    finally:
        if original:
            TOOLS["browser_snapshot"] = original


def test_browser_profile_resolution(monkeypatch):
    from aap import settings
    from aap.tools.browser import resolve_profile
    from aap.models import AgentSpec, AgentTask

    class Ctx:
        def __init__(self, instr, context=None):
            self.task = AgentTask(task_id="1", agent_id="browser_agent", instruction=instr, context=context or [])
            self.spec = AgentSpec(id="browser_agent", name="B")
    settings.update({"browser_profile": "saved"})
    assert resolve_profile(Ctx("open youtube")) == ("saved", "preference")
    assert resolve_profile(Ctx("Use Chrome profile asdf and open youtube")) == ("asdf", "request")
    st = execstate.new_state("x", profile="fromstate")
    assert resolve_profile(Ctx("open youtube", [execstate.as_context(st)])) == ("fromstate", "task state")
    assert resolve_profile(Ctx("open youtube"), "explicit") == ("explicit", "argument")
    assert all(resolve_profile(Ctx(s))[0] for s in ("", "hi"))   # never ''


def test_browser_open_tool_registration():
    import aap.tools  # noqa: F401
    from aap.core.sdk import TOOLS
    assert TOOLS["browser_open"].fn.__name__ == "browser_open"   # decorator on the right function
    assert "resolve_profile" not in TOOLS


def test_browser_graph_stops_when_profile_cannot_open():
    from aap.graphs.browser import _build
    from aap.models import AgentSpec
    g = _build(AgentSpec(id="b", name="B", graph="browser"))
    edges = {(e.source, e.target) for e in g.compile().get_graph().edges}
    assert ("start", "finalize") in edges and ("decide", "verify") in edges and ("verify", "observe") in edges


def test_bridge_switches_profile_instead_of_silent_reuse(monkeypatch, tmp_path):
    from bridge import chrome
    alive = {"v": True}
    monkeypatch.setattr(chrome, "cdp_alive", lambda port=chrome.CDP_PORT: alive["v"])
    monkeypatch.setattr(chrome, "find_chrome", lambda: "chrome.exe")
    started = []

    class P:
        def __init__(self, args, close_fds=True):
            started.append(args)
            alive["v"] = True          # Chrome comes up
        def terminate(self):
            alive["v"] = False
        def wait(self, timeout=0):
            return 0
    monkeypatch.setattr(chrome.subprocess, "Popen", P)
    # unknown Chrome already on the debug port -> refuse (don't attach to an unknown profile)
    chrome._active.update(user_data_dir=None, profile=None, proc=None)
    try:
        chrome.launch(tmp_path / "a", "a")
        assert False, "should refuse"
    except RuntimeError:
        pass
    # ours on profile a, user asks for b -> stop a, start b
    alive["v"] = False
    r1 = chrome.launch(tmp_path / "a", "a")
    assert r1["profile"] == "a"
    r_same = chrome.launch(tmp_path / "a", "a")
    assert r_same["already_running"] and not r_same["switched"]
    r2 = chrome.launch(tmp_path / "b", "b")
    assert r2["switched"] and r2["profile"] == "b" and chrome.active()["profile"] == "b"


def test_cheapest_fit_and_no_repeat_plans():
    from aap.graphs.main import _cheapest_fit, _no_repeat_plans
    active = {"competitive_programming_agent": 1}
    s = _cheapest_fit({"agent_id": "coding_agent", "instruction": "Solve LeetCode Two Sum in Python"}, active)
    assert s["agent_id"] == "competitive_programming_agent"
    assert _cheapest_fit({"agent_id": "coding_agent", "instruction": "fix the tests in /workspace/app"},
                         active)["agent_id"] == "coding_agent"
    steps = [{"id": "s1", "agent_id": "a", "instruction": "Do X"}, {"id": "s2", "agent_id": "b", "instruction": "Do Y"}]
    reps = {"s1": {"status": "success"}, "s2": {"status": "failed", "errors": ["timeout"]}}
    out = _no_repeat_plans([{"id": "f1", "agent_id": "a", "instruction": "do x"},
                            {"id": "f2", "agent_id": "b", "instruction": "Do Y"}], steps, reps)
    assert [f["id"] for f in out] == ["f2"] and "timeout" in out[0]["instruction"] and "DIFFERENT" in out[0]["instruction"]


def test_paste_inputs_reach_browser_agent():
    from aap.graphs import _common as c
    from aap.graphs.browser import _PASTE_RE, extract_code
    state = {"task": {"instruction": "Open https://leetcode.com/problems/two-sum/ and paste the solution into the "
                                     "code editor",
                      "context": [{"kind": "exec_state", "state": {}},
                                  {"agent_id": "competitive_programming_agent", "summary": "solved",
                                   "result": "```python\nclass Solution:\n    pass\n```"}]}}
    inputs = c.context_text(state)
    assert "class Solution" in inputs and "exec_state" not in inputs
    assert _PASTE_RE.search(state["task"]["instruction"])
    assert extract_code(inputs).startswith("class Solution")


def test_tab_count_only_for_opening_tabs():
    from aap.graphs.browser import tab_count
    assert tab_count("i want u to open 5 tabs of youtube in chrome") == 5
    assert tab_count("in the sandbox browser, open 25 tabs of wikipedia") == 25
    assert tab_count("For each of the 5 LeetCode problem tabs, paste the solution into the code editor") == 0
    assert tab_count("Verify final state: cycle through all 5 open tabs") == 0


def test_browser_open_not_loop_guarded():
    from aap.core.sdk import _OBSERVE_TOOLS
    assert "browser_open" in _OBSERVE_TOOLS


def test_planner_text_profile_must_be_known(monkeypatch):
    from aap import settings
    from aap.tools import browser as tb
    from aap.models import AgentSpec, AgentTask
    settings.update({"browser_profile": "saved"})
    monkeypatch.setattr(tb, "_known_profile", lambda n: n.lower() in {"asdf", "saved"})

    class Ctx:
        def __init__(self, instr, parent=""):
            self.task = AgentTask(task_id="1", agent_id="browser_agent", instruction=instr, parent_agent=parent)
            self.spec = AgentSpec(id="browser_agent", name="B")
    # planner-written: an unknown name is ignored (no 'logged'/'xyz' profiles invented)
    assert tb.resolve_profile(Ctx("open it in profile xyz", "main_agent")) == ("saved", "preference")
    assert tb.resolve_profile(Ctx("use the asdf profile", "main_agent")) == ("asdf", "request")
    # the user's own words when running the agent directly are trusted
    assert tb.resolve_profile(Ctx("use profile xyz")) == ("xyz", "request")
    from aap.core.execstate import parse_profile
    assert parse_profile("open it in your logged-in Chrome profile") == ""


@__import__("pytest").mark.skipif(__import__("sys").platform != "win32", reason="Windows desktop bridge only")
def test_desktop_refuses_while_owner_active(monkeypatch):
    import pytest
    from bridge import desktop
    monkeypatch.setattr(desktop, "user_idle_seconds", lambda: 2.0)    # owner typed 2 s ago
    desktop._last_agent_input[0] = 0.0
    with pytest.raises(RuntimeError, match="using the computer"):
        desktop.act("click", x=10, y=10)
    monkeypatch.setattr(desktop, "user_idle_seconds", lambda: 120.0)  # away for 2 minutes
    assert desktop.owner_active() is False


def test_experience_record_and_recall(tmp_path, monkeypatch):
    from aap import config
    from aap.core import experience
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path)
    xs = execstate.new_state("submit two sum on leetcode", profile="asdf")
    xs["failed"] = [{"action": "paste in C++ editor", "error": "Compile Error: editor language was C++"}]
    xs["completed"] = [{"step": "s1", "summary": "switched editor to Python3 and submitted", "verified": True}]
    sid = experience.record("submit two sum on leetcode", ["desktop_agent"], xs, "success")
    sid2 = experience.record("submit two sum on leetcode", ["desktop_agent"], xs, "success")
    assert sid == sid2 and (tmp_path / sid / "SKILL.md").exists()          # merged, not duplicated
    md = (tmp_path / sid / "SKILL.md").read_text(encoding="utf-8")
    assert "## DON'T" in md and "C++" in md and "uses: 2" in md
    text, ids = experience.recall("please submit leetcode two sum again")
    assert ids == [sid] and "DON'T" in text and "C++" in text
    assert experience.recall("what's the weather")[1] == []


def test_similarity_stop_is_strict():
    from aap.graphs.main import _similar, _non_retryable
    assert _similar("hi", "Hi") == 1.0
    assert _similar("browser_agent:failed:timeout on page", "browser_agent:failed:timeout on page x") < 0.95
    assert _non_retryable({"summary": "x", "errors": ["you're using the computer (last input 1s ago)"]})
    assert not _non_retryable({"summary": "Wrong Answer on case 3", "errors": []})


def test_general_computer_use_tools_registered():
    import aap.tools  # noqa: F401
    from aap.core.sdk import TOOLS
    from aap.agents.prebuilt.catalog import PREBUILT
    for t in ("chrome_open_profile", "desktop_wait_for", "desktop_act", "desktop_tree"):
        assert t in TOOLS
    assert "leetcode_submit" not in TOOLS and not any(a.id == "leetcode_agent" for a in PREBUILT)
    comp = next(a for a in PREBUILT if a.id == "desktop_agent")
    assert {"chrome_open_profile", "desktop_wait_for"} <= set(comp.tools)


def test_desktop_approval_once_per_task():
    from aap.core import approvals
    assert approvals._PER_ACTION == ()     # one approval per task, not per click


def test_auto_mode_approves_but_dangerous_still_asks():
    from aap import events, registry, settings
    from aap.core import approvals
    registry.init()
    settings.update({"auto_mode": True})
    run = events.create(registry.new_run("t", "x"))
    assert approvals.request(run, "a", "desktop_act: let X control your mouse & keyboard for this task", {})
    assert approvals._ALWAYS_ASK.search("run_command: potentially destructive command: rm -rf /")
    assert approvals._ALWAYS_ASK.search("browser_act: sensitive browser action (click 'Place order')")


def test_memory_scopes(tmp_path, monkeypatch):
    from aap import config
    from aap.memory import store
    store._local.__dict__.clear()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store.add("user prefers python", "fact", scope="global")
    store.add("leetcode editor defaults to C++ — switch language first", "episode", scope="desktop_agent")
    store.add("research: cite sources", "episode", scope="web_research_agent")
    mine = store.search("python editor language sources", 10, scope="desktop_agent")
    texts = " ".join(m["text"] for m in mine)
    assert "python" in texts and "C++" in texts and "cite" not in texts      # own + global, not other agents'
    assert {m["scope"] for m in store.list_all()} == {"global", "desktop_agent", "web_research_agent"}
    store._local.__dict__.clear()


def test_agent_writes_and_recalls_own_memory(tmp_path, monkeypatch):
    from aap import config
    from aap.memory import store
    from aap.core import runtime as rt
    from aap.models import AgentSpec, AgentTask, Report
    store._local.__dict__.clear()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    spec = AgentSpec(id="web_research_agent", name="Web Research")
    task = AgentTask(task_id="1", agent_id=spec.id, instruction="research openai dots launch")
    rt._write_own_memory(spec, task, Report(agent_id=spec.id, task_id="1", status="failed",
                                            summary="openai.com blocked bots", errors=["403 from openai.com"]))
    notes = rt._own_memory_notes(spec.id, "research openai dots features")
    assert "403" in notes and "openai" in notes.lower()
    assert rt._own_memory_notes("writer_agent", "research openai dots") == ""   # other agents don't see it
    store._local.__dict__.clear()


def test_over_budget_asks_user_never_drops_steps(monkeypatch):
    from aap import routing
    from aap.core import questions
    from aap.graphs import main as m

    class Run:
        run_id = "budget-test"
    class Ctx:
        run = Run()
        def emit(self, *a, **k):
            self.msg = a[0]
    asked = []
    def steps():
        return [{"id": f"s{i}", "agent_id": "web_research_agent", "instruction": "x", "est_tokens": 20000}
                for i in range(5)]
    # user says "use more" (also the default when unanswered) -> full quality, budget raised, nothing dropped
    monkeypatch.setattr(questions, "ask", lambda run, a, qs, timeout=0: asked.append(qs) or [qs[0]["default"]])
    out = m._allocate_budget(Ctx(), {"effort": 1}, steps())
    assert len(out) == 5 and all(s.get("effort", -1) == -1 for s in out) and asked
    assert routing.budget_for("budget-test", 1) >= sum(s["est_tokens"] for s in out)
    # user says "keep it lighter" -> every step still runs, just lighter
    routing._run_budget.pop("budget-test", None)
    monkeypatch.setattr(questions, "ask", lambda run, a, qs, timeout=0: ["Keep it lighter"])
    out = m._allocate_budget(Ctx(), {"effort": 1}, steps())
    assert len(out) == 5 and all(s["effort"] == 0 for s in out)
    # the user's own effort choice is never changed and they aren't asked
    asked.clear()
    monkeypatch.setattr(questions, "ask", lambda *a, **k: asked.append(1) or ["Keep it lighter"])
    out = m._allocate_budget(Ctx(), {"effort": 1, "user_effort": True}, steps())
    assert not asked and all(s.get("effort", -1) == -1 for s in out)

def test_questions_answer_and_default():
    import threading
    from aap import events, registry
    from aap.core import questions
    registry.init()
    run = events.create(registry.new_run("t", "x"))
    qs = [{"q": "What to focus on?", "options": ["features", "pricing"], "default": "features"}]
    out = {}
    th = threading.Thread(target=lambda: out.update(a=questions.ask(run, "main_agent", qs, timeout=10)))
    th.start()
    import time
    for _ in range(50):
        p = questions.pending(run.run_id)
        if p:
            break
        time.sleep(0.05)
    assert questions.answer(p[0]["id"], ["pricing"])
    th.join(5)
    assert out["a"] == ["pricing"]
    assert questions.ask(run, "main_agent", qs, timeout=0.5) == ["features"]   # unanswered → default
