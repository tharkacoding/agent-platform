"""End-to-end Main Agent flow in offline (mock LLM) mode with stub worker agents.

Covers: greeting fast-path (tier 0), task → discovery → plan → parallel Send fan-out → review → synthesis,
missing capability → Agent Builder detour → re-plan with the new agent, schedule intent, cancellation-free run.
"""
import os
import tempfile
import time

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_main_"))

import pytest  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from typing import TypedDict  # noqa: E402

from aap import events, registry, settings  # noqa: E402
from aap.core.runtime import Runtime  # noqa: E402
from aap.core.sdk import archetype, get_ctx  # noqa: E402
from aap.models import AgentSpec, AgentTask  # noqa: E402

settings.update({"llm_provider": "mock"})


class S(TypedDict, total=False):
    task: dict
    report: dict


@archetype("stub_echo")
def _stub(spec):
    def work(state, config):
        ctx = get_ctx(config)
        ctx.node("work")
        time.sleep(0.05)
        return {"report": ctx.report(f"{spec.name} handled it", result=f"{spec.id} result for: "
                                     f"{ctx.task.instruction[:40]} (effort {ctx.effort})")}
    g = StateGraph(S)
    g.add_node("work", work)
    g.add_edge(START, "work")
    g.add_edge("work", END)
    return g


@archetype("stub_builder")
def _builder(spec):
    def build(state, config):
        ctx = get_ctx(config)
        ctx.node("build")
        new = AgentSpec(id="learning_progress_agent", name="Learning Progress Agent", type="custom",
                        description="Analyzes programming learning progress and creates weekly learning plans",
                        capabilities=["progress_analysis", "learning_plans", "data_analysis"], graph="stub_echo",
                        created_by="agent_builder")
        ctx.runtime.register(new)
        return {"report": ctx.report("Built Learning Progress Agent", result="registered learning_progress_agent",
                                     files_created=["learning_progress_agent/v1/manifest.yaml"])}
    g = StateGraph(S)
    g.add_node("build", build)
    g.add_edge(START, "build")
    g.add_edge("build", END)
    return g


@pytest.fixture(scope="module")
def rt():
    return make_runtime()


def make_runtime():
    import aap.graphs.main  # noqa: F401  registers 'main'
    registry.init()
    r = Runtime()
    for spec in [
        AgentSpec(id="main_agent", name="Main Agent", type="system", graph="main"),
        AgentSpec(id="agent_builder", name="Agent Builder", type="system", graph="stub_builder"),
        AgentSpec(id="data_analysis_agent", name="Data Analysis Agent", graph="stub_echo",
                  description="Statistics and data analysis of CSV/JSON datasets",
                  capabilities=["data_analysis", "statistics"]),
        AgentSpec(id="coding_agent", name="Coding Agent", graph="stub_echo", description="Writes code",
                  capabilities=["coding", "python"]),
        AgentSpec(id="writer_agent", name="Writing Agent", graph="stub_echo", description="Writes text",
                  capabilities=["writing", "summarization"]),
    ]:
        registry.upsert_agent(spec)
    return r


def _run(rt, msg, effort=-1):
    run_id = registry.new_run("chat", msg)
    run = events.create(run_id)
    rep = rt.run_agent(AgentTask(task_id="t1", agent_id="main_agent", instruction=msg, effort=effort), run)
    return rep, run


def test_greeting_fast_path(rt):
    rep, run = _run(rt, "hi")
    assert rep.status == "success" and rep.result
    nodes = [e["data"].get("node") for e in run.events if e["type"] == "node"]
    assert "respond" in nodes and "plan_dag" not in nodes
    intent = next(e for e in run.events if e["message"].startswith("Intent"))
    assert intent["data"]["effort"] == 0


def test_task_parallel_delegation(rt):
    rep, run = _run(rt, "Analyze this programming data and write the code for a python script: 3,5,8,13")
    agents = {e["agent_id"] for e in run.events if e["type"] == "agent" and e["status"] == "done"}
    assert {"data_analysis_agent", "coding_agent"} <= agents
    assert any(e["type"] == "plan" for e in run.events)
    assert any(e["type"] == "token" for e in run.events)   # streamed final answer
    # success only when every TODO is verified; other registered agents (full suite) may be unverified -> partial
    assert rep.status in ("success", "partial") and "result" in str(rep.result)


def test_builds_missing_agent_then_uses_it(rt):
    rep, run = _run(rt, "Create an agent that monitors my programming learning progress and makes weekly plans")
    started = [e["agent_id"] for e in run.events if e["type"] == "agent" and e["status"] == "running"]
    assert "agent_builder" in started
    assert registry.get_agent("learning_progress_agent") is not None
    assert started.index("agent_builder") < started.index("learning_progress_agent")


def test_schedule_intent(rt):
    rep, run = _run(rt, "Every monday at 9am summarize my week")
    assert "Scheduled" in rep.result
    assert registry.list_schedules()


def test_forced_effort_propagates(rt):
    rep, run = _run(rt, "Analyze this data: 1,2,3", effort=3)
    assert "(effort 3)" in str(rep.result) or any("effort 3" in str(r.get("result")) for r in
                                                  registry.reports_for_run(run.run_id))
