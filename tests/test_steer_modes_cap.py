"""User token cap, slash modes, mid-run steering, learned model health (offline / mock LLM)."""
import os
import tempfile
import time

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_steer_"))

import pytest  # noqa: E402

from aap import events, modes, registry, routing  # noqa: E402
from aap.models import AgentTask  # noqa: E402
from tests.test_main_flow import make_runtime  # noqa: E402


@pytest.fixture(scope="module")
def rt():
    return make_runtime()


def _run(rt, msg, effort=-1, before=None):
    run_id = registry.new_run("chat", msg)
    run = events.create(run_id)
    if before:
        before(run)
    rep = rt.run_agent(AgentTask(task_id="t1", agent_id="main_agent", instruction=msg, effort=effort), run)
    return rep, run


# ---------------------------------------------------------------- token cap the user gives
@pytest.mark.parametrize("text,n", [("use only 10 tokens for fun", 10), ("within 2k tokens", 2000),
                                    ("500 tokens max please", 500), ("limit to 1,500 tokens", 1500),
                                    ("what are tokens in nlp", 0), ("I have 10 tokens in my game", 0)])
def test_parse_user_cap(text, n):
    assert routing.parse_user_cap(text) == n


def test_user_cap_is_hard_even_when_budget_mode_off():
    rid = "cap-test"
    routing.set_user_cap(rid, 10)
    assert routing.cap_left(rid) == 10 and not routing.over_budget(rid, 2)
    tok = routing.set_run(rid)
    try:
        routing.record("m", 500, 10)
        assert routing.clamp_max_tokens(4000) == 1
    finally:
        routing.reset_run(tok)
    assert routing.over_budget(rid, 2) and routing.cap_left(rid) == 0
    assert routing.cap_left("no-cap-run") is None and routing.clamp_max_tokens(300, "no-cap-run") == 300


def test_tiny_cap_answers_directly_and_reports_usage(rt):
    rep, run = _run(rt, "explain black holes, use only 10 tokens for fun")
    msgs = [e["message"] for e in run.events]
    assert any(m.startswith("Token limit: 10") for m in msgs)
    nodes = [e["data"].get("node") for e in run.events if e["type"] == "node"]
    assert "respond" in nodes and "plan_dag" not in nodes          # no planning/agents within 10 tokens
    assert "10-token limit" in (rep.result or "")
    assert not any("Use more tokens" in str(e["data"]) for e in run.events)   # never asks to exceed the user's cap


# ---------------------------------------------------------------- slash modes
def test_hundred_modes_and_parse():
    assert len(modes.MODES) == 100
    ms, rest, unknown = modes.parse("/deep-dive /fact-check is coffee bad for you")
    assert [m["id"] for m in ms] == ["deep-dive", "fact-check"] and rest == "is coffee bad for you" and not unknown
    assert modes.parse("/soul teacher")[0] == []                 # reserved commands are not modes
    assert "plan" in modes.suggest("plna")
    assert modes.resolve("tldr") == "summarize"


def test_modes_command_lists(rt):
    rep, _ = _run(rt, "/modes coding")
    assert "`/debug`" in rep.result and "`/deep-dive` —" not in rep.result


def test_unknown_mode_suggests(rt):
    rep, _ = _run(rt, "/plna my week")
    assert "/plan" in rep.result


def test_mode_sets_directives_effort_and_intent(rt):
    rep, run = _run(rt, "/statistical analyze: solved 12 DP, 30 greedy, 4 graph problems")
    ev = next(e for e in run.events if e["message"].startswith("Modes:"))
    assert ev["data"]["modes"] == ["statistical"] and ev["data"]["intent"] == "task"
    assert "Statistical Mode" in run.directives
    assert rep.status in ("success", "partial")


def test_chat_mode_answers_directly(rt):
    _, run = _run(rt, "/name-harvest a coffee shop")
    nodes = [e["data"].get("node") for e in run.events if e["type"] == "node"]
    assert "respond" in nodes and "plan_dag" not in nodes


# ---------------------------------------------------------------- talk while it works
def test_steer_block_and_tool_injection():
    run = events.create("steer-unit")
    assert run.steer_block() == ""
    run.add_steer("make the header blue")
    assert "make the header blue" in run.steer_block() and "OVERRIDE" in run.steer_block()
    assert run.steer_block(1) == ""
    assert any(e["type"] == "steer" for e in run.events)


def test_mid_run_message_replans(rt):
    # message arrives before the first dispatch → the plan is rewritten and every step carries the new order
    rep, run = _run(rt, "Analyze this data: solved 12 DP, 30 greedy problems and write a short summary",
                    before=lambda r: r.add_steer("also compare with last month: 8 DP, 20 greedy"))
    msgs = [e["message"] for e in run.events]
    assert any(m.startswith("Changed course") for m in msgs)
    plans = [e for e in run.events if e["type"] == "plan" and e["agent_id"] == "main_agent"]
    assert any("last month" in s["instruction"] for s in plans[-1]["data"]["plan"]["steps"])
    assert rep.status in ("success", "partial")


# ---------------------------------------------------------------- learned model health
def test_failing_model_moves_back_and_escalates():
    routing._cool.clear(); routing._fails.clear(); routing._ok_at.clear()
    chain = routing.chain("main", 2)
    first = chain[0]
    routing.cool_down(first, 60, "500 boom")
    t1 = routing._cool[first] - time.time()
    routing._cool[first] = 0                       # cool-down expired …
    assert routing.chain("main", 2)[0] != first    # … but a model that keeps failing still goes after untried ones
    routing.cool_down(first, 60, "500 boom")
    assert routing._cool[first] - time.time() > t1 * 1.5   # escalating back-off
    routing.mark_ok(first)
    assert routing.chain("main", 2)[0] == first and not routing._fails.get(first)


# ---------------------------------------------------------------- scheduled jobs are runs, not chats
def test_scheduled_run_creates_no_conversation(rt):
    from aap.core import runtime as rtmod
    before = {c["id"] for c in registry.list_conversations(500)}
    done = []
    rid = rtmod.runtime.start_run("tell om to drink water", kind="schedule", on_done=lambda r, rep, f: done.append(f))
    for _ in range(100):
        if done:
            break
        time.sleep(0.1)
    assert done and registry.get_run(rid)["kind"] == "schedule"
    assert {c["id"] for c in registry.list_conversations(500)} == before


def test_pushes_only_for_owner_runs_and_master_switch(monkeypatch):
    from aap import notify, settings
    sent = []
    monkeypatch.setattr(settings, "get", lambda k, d=None: {"notify_enabled": False, "notify_ntfy_url": "http://x",
                                                            "notify_on": "all", "llm_provider": "openai"}.get(k, d))
    assert notify.send("t", "b") is False                      # master switch off → nothing
    monkeypatch.setattr(settings, "get", lambda k, d=None: {"notify_enabled": True, "notify_ntfy_url": "http://x",
                                                            "notify_on": "all", "llm_provider": "openai"}.get(k, d))
    monkeypatch.setattr("threading.Thread.start", lambda self: sent.append(1))
    assert notify.send("t", "b") is True and sent
    assert events.create("api-run").notify is False            # runs not started by the owner never push


def test_computer_agent_steps_never_run_in_parallel():
    from aap.graphs.main import _ready
    plan = {"steps": [{"id": "a", "agent_id": "desktop_agent", "depends_on": []},
                      {"id": "b", "agent_id": "desktop_agent", "depends_on": []},
                      {"id": "c", "agent_id": "coding_agent", "depends_on": []}]}
    assert [s["id"] for s in _ready({"plan": plan, "reports": {}, "running": []})] == ["a", "c"]
    assert [s["id"] for s in _ready({"plan": plan, "reports": {}, "running": ["a"]})] == ["c"]
    assert [s["id"] for s in _ready({"plan": plan, "reports": {"a": {}}, "running": ["a"]})] == ["b", "c"]


# ---------------------------------------------------------------- 2c fixes
def test_text_question_tail_becomes_card_material():
    from aap.graphs.main import split_text_question
    body, q = split_text_question("Blocked.\n\n**Option A: Manual fix**\nDo it yourself.\n\n**Option B: Retry via API**\n"
                                  "I fetch it.\n\nWhich would you prefer?")
    assert body == "Blocked." and q["options"] == ["Manual fix", "Retry via API"]
    assert split_text_question("Accepted 65/65.")[1] is None


def test_steps_with_failed_inputs_are_blocked_not_run_blind():
    from aap.graphs.main import _blocked_reports
    st = {"plan": {"steps": [{"id": "s1", "agent_id": "desktop_agent", "depends_on": []},
                             {"id": "s2", "agent_id": "coding_agent", "depends_on": ["s1"]},
                             {"id": "s3", "agent_id": "coding_agent", "depends_on": []}]},
          "reports": {"s1": {"status": "partial", "result": ""}}, "running": []}
    out = _blocked_reports(st)
    assert list(out) == ["s2"] and out["s2"]["status"] == "failed" and "blocked" in out["s2"]["errors"][0]
    st["reports"]["s1"] = {"status": "partial", "result": "problem: two sum ..."}
    assert _blocked_reports(st) == {}


def test_agent_saying_it_was_blocked_is_not_a_success():
    from aap.core.sdk import _BLOCKED_RE
    assert _BLOCKED_RE.search("Blocked upstream — no problem statement exists")
    assert _BLOCKED_RE.search("I cannot proceed without the problem statement")
    assert not _BLOCKED_RE.search("Solved with a hash map in O(n)")
