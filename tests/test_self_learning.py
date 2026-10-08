"""Owner 2026-10-08: AgentOS learns by itself — site aliases from visited pages, post-mortem lessons from failures
that the next run on the same site reads back."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_learn_"))

from aap import events, site_aliases  # noqa: E402
from aap.core import execstate, postmortem  # noqa: E402


def test_site_aliases_are_learned_from_visits():
    assert site_aliases.name_of("https://www.hackerearth.com/problem/x") == "hackerearth"
    assert site_aliases.name_of("http://127.0.0.1:8000/") == "" and site_aliases.name_of("https://localhost/") == ""
    assert site_aliases.name_of("https://www.bbc.co.uk/news") == "bbc"
    assert site_aliases.resolve("kattis") is None
    site_aliases.learn("https://open.kattis.com/problems/hello")
    assert site_aliases.resolve("kattis") == "https://open.kattis.com"
    assert "open.kattis.com" in site_aliases.domains_in("solve hello on kattis")
    from aap.graphs.main import _names_site
    assert _names_site("play the first video on kattis")              # router now knows the learned site


def test_postmortem_writes_lessons_the_next_run_reads():
    run = events.create("pm-1")
    run.emit("node", "browser_agent", "running", 'goto {"url": "https://leetcode.com/problems/two-sum/"}',
             url="https://leetcode.com/problems/two-sum/")
    run.emit("supervisor", "main_agent", "info", "Main Agent check on browser_agent (step 5): repeating the same "
             "action 3x in the last 8 steps → redirect")
    run.emit("log", "browser_agent", "failed", "NOT verified: no verdict on the page")
    found = postmortem.record(run, "submit two sum on leetcode", "partial", estimate=10_000, used=50_000)
    cats = {f["category"] for f in found}
    assert {"LOOP", "NOT_VERIFIED", "OVER_BUDGET"} <= cats
    lessons = execstate.lessons_for(["leetcode.com"], 10)
    assert any("[LOOP]" in ln and "change strategy" in ln for ln in lessons)
    assert any(e["message"].startswith("Post-mortem") for e in run.events)


def test_clean_success_teaches_nothing_new():
    run = events.create("pm-2")
    run.emit("log", "browser_agent", "done", "Seen on screen: 'Accepted'")
    assert postmortem.record(run, "submit", "success", 10_000, 9_000) == []
