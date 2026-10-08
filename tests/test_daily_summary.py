"""Daily summary push: counts today's runs, lists failures first, built-in 21:00 schedule sends it (no LLM)."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_daily_"))

from aap import daily_summary, registry  # noqa: E402
from aap.scheduler import jobs  # noqa: E402


def test_summary_lists_failures_and_done():
    a = registry.new_run("chat", "solve two sum on leetcode"); registry.finish_run(a, "done", "Accepted")
    b = registry.new_run("chat", "play songs on youtube"); registry.finish_run(b, "failed", "player blocked")
    title, body = daily_summary.build()
    assert "failed" in title and "Failed" in body and "play songs on youtube" in body
    assert body.index("Failed") < body.index("Done:") and "solve two sum" in body


def test_builtin_schedule_sends_without_a_run(monkeypatch):
    jobs.ensure_daily_summary(); jobs.ensure_daily_summary()   # idempotent
    s = [x for x in registry.list_schedules() if x["id"] == "daily_summary"]
    assert len(s) == 1 and s[0]["cron"] == "0 21 * * *"
    sent = []
    monkeypatch.setattr(daily_summary, "send", lambda: sent.append(1) or True)
    before = len(registry.list_runs(500))
    assert jobs.run_now(s[0]) == "summary-sent" and sent and len(registry.list_runs(500)) == before
