"""Capability self-test plumbing (aap/selftest.py, /api/selftest, nightly 'selftest' schedule), offline."""
import json
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_selftest_"))

import pytest  # noqa: E402

from aap import registry, selftest  # noqa: E402
from aap.scheduler import jobs  # noqa: E402

registry.init()

ROWS = {
    "16.1": {"status": "pass", "title": "hi", "evidence": "effort=0", "tokens": 10, "seconds": 3.0},
    "16.2": {"status": "fail", "title": "trip", "evidence": "text question left", "run_id": "r2", "tokens": 500, "seconds": 40},
    "16.10": {"status": "skipped", "title": "screen", "note": "owner busy", "seconds": 0},
    "16.11": {"status": "error", "title": "boom", "evidence": "KeyError", "seconds": 1},
    "1.1": {"status": "pass", "title": "old section", "seconds": 2},
}


@pytest.fixture
def results():
    selftest.DIR.mkdir(parents=True, exist_ok=True)
    selftest.RESULTS.write_text(json.dumps(ROWS), encoding="utf-8")
    selftest.STATE.unlink(missing_ok=True)
    yield
    selftest.RESULTS.unlink(missing_ok=True)


def test_summary_counts_failures_tokens(results):
    s = selftest.summary()
    assert (s["pass"], s["fail"], s["skipped"], s["error"], s["total"]) == (2, 1, 1, 1, 5)
    assert [f["id"] for f in s["failures"]] == ["16.11", "16.2"] and s["tokens"] == 510 and s["seconds"] == 46


def test_finished_pushes_once_and_logs_open_failures(results, monkeypatch):
    sent = []
    monkeypatch.setattr("aap.notify.send", lambda *a, **k: sent.append((a, k)) or True)
    selftest.FAILS.unlink(missing_ok=True)
    text = selftest.finished(["16"])
    assert text.startswith("Capability tests: 1/3 pass (1 skipped)") and "16.2" in text and "1.1" not in text
    assert len(sent) == 1 and sent[0][1]["priority"] == "high"
    fails = selftest.FAILS.read_text(encoding="utf-8")
    assert "16.2 trip (fail) run=r2" in fails and "16.11" in fails
    assert any(c["source"] == "selftest" for c in registry.list_feed())


class _P:
    pid = 4242

    def wait(self):
        return 0


def test_start_launches_harness_and_refuses_second(results, monkeypatch):
    calls = []
    monkeypatch.setattr(selftest.subprocess, "Popen", lambda *a, **k: calls.append((a, k)) or _P())
    monkeypatch.setattr(selftest, "_watch", lambda *a: None)
    monkeypatch.setenv("AAP_SELF_URL", "http://127.0.0.1:7870")
    st = selftest.start(["16"])
    assert st["pid"] == 4242 and st["sections"] == ["16"]
    argv, kw = calls[0]
    assert argv[0][-2:] == [str(selftest.HARNESS), "16"]
    env = kw["env"]
    assert env["AOS_API"] == "http://127.0.0.1:7870" and env["AOS_RESULTS"] == str(selftest.RESULTS)
    assert env["AOS_PROGRESS"] == str(selftest.PROGRESS) and env["AOS_DATA"] == str(selftest.config.DATA_DIR)
    assert env["AOS_SETTINGS"].endswith("settings.json") and env["AOS_HOME"]
    monkeypatch.setattr(selftest, "_alive", lambda pid: pid == 4242)
    assert selftest.state()["running"]
    with pytest.raises(RuntimeError):
        selftest.start(["16"])
    assert len(calls) == 1


def test_api_get_and_run(results, monkeypatch):
    from fastapi.testclient import TestClient
    from aap.api.app import app
    client = TestClient(app)
    d = client.get("/api/selftest").json()
    assert d["summary"]["fail"] == 1 and "16.2" in d["rows"] and d["state"]["running"] is False
    started = []
    monkeypatch.setattr(selftest, "start", lambda sections: started.append(sections) or {"pid": 1, "sections": sections, "started": 1.0})
    assert client.post("/api/selftest/run", json={"sections": ["16.2"]}).status_code == 200 and started == [["16.2"]]

    def busy(sections):
        raise RuntimeError("a self-test is already running")
    monkeypatch.setattr(selftest, "start", busy)
    assert client.post("/api/selftest/run", json={}).status_code == 409


def test_nightly_schedule_is_builtin_and_runs_selftest_not_chat(monkeypatch):
    jobs.ensure_selftest()
    jobs.ensure_selftest()   # idempotent
    rows = [s for s in registry.list_schedules() if s["id"] == "selftest"]
    assert len(rows) == 1 and rows[0]["cron"] == "0 3 * * *" and rows[0]["enabled"] and rows[0]["kind"] == "selftest"
    started = []
    monkeypatch.setattr(selftest, "start", lambda sections: started.append(sections) or {"pid": 1, "sections": sections, "started": 123.0})
    monkeypatch.setattr("aap.core.runtime.runtime.start_run", lambda *a, **k: pytest.fail("selftest must not start an agent run"))
    from fastapi.testclient import TestClient
    from aap.api.app import app
    r = TestClient(app).post("/api/schedules/selftest/run").json()
    assert r["run_id"] == "selftest-123" and started == [["16"]]
    assert next(s for s in registry.list_schedules() if s["id"] == "selftest")["last_run_id"] == "selftest-123"
