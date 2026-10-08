"""16.6: Stop must work between any two graph nodes and before any model call — not only at tool calls."""
import os
import tempfile

import pytest

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_cancel_"))

from aap import events, registry, routing  # noqa: E402

registry.init()   # events are persisted: don't depend on another test having created the tables
from aap.llm import llm  # noqa: E402


def test_cancelled_run_makes_no_more_model_calls():
    run = events.create("cancel-1")
    tok = routing.set_run("cancel-1")
    try:
        assert llm.json("s", "u", fallback=lambda: {"ok": 1}) == {"ok": 1}     # running: normal
        run.cancelled = True
        with pytest.raises(events.RunCancelled):
            llm.json("s", "u", fallback=lambda: {"ok": 1})
        with pytest.raises(events.RunCancelled):
            llm.complete("s", "u", fallback=lambda: "x")
    finally:
        routing.reset_run(tok)


def test_cancelled_run_stops_at_the_next_node():
    from aap.core.sdk import AgentContext
    from aap.agents.prebuilt.catalog import PREBUILT
    from aap.models import AgentTask
    run = events.create("cancel-2")
    ctx = AgentContext(spec=PREBUILT[0], task=AgentTask(task_id="t", agent_id="main_agent", instruction="x"),
                       run=run, runtime=None)
    ctx.node("a", "first")
    run.cancelled = True
    with pytest.raises(events.RunCancelled):
        ctx.node("b", "second")


def test_model_outage_is_reported_not_faked(monkeypatch):
    from aap import routing
    tok = routing.set_run("outage-1")
    try:
        assert not routing.models_down("outage-1")
        routing.note_all_failed(); routing.note_all_failed()
        assert routing.models_down("outage-1")
        routing.record("m", 10)                       # one real answer → not an outage any more
        assert not routing.models_down("outage-1")
    finally:
        routing.reset_run(tok)


def test_failover_keeps_going_past_the_first_chain(monkeypatch):
    """Owner: 'it should take another model if this is not working' — the first 8+ models all fail, a later one works."""
    import httpx
    from openai import APIStatusError
    from aap import llm as llm_mod, routing
    L = llm_mod.LLM()
    chain = [f"bad/m{i}" for i in range(10)]
    monkeypatch.setattr(L, "chain", lambda role="main": chain)
    monkeypatch.setattr(L, "_known", lambda: chain + ["good/model"])
    monkeypatch.setattr(routing, "fallback_pool", lambda exclude, known=None, limit=16: ["good/model"])
    calls = []

    class Resp:
        status_code = 429
        request = httpx.Request("POST", "http://x")
        headers = {}

    def fake_try(m, kw, role):
        calls.append(m)
        if m == "good/model":
            return ("OK", m)
        return APIStatusError("rate limited", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)
    monkeypatch.setattr(L, "_try_model", fake_try)
    r, used = L._create("main", messages=[])
    assert r == "OK" and used == "good/model" and calls[:8] == chain[:8] and calls[-1] == "good/model"
