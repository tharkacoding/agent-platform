"""Regression for live case 5.3: browser agent looped on scroll and the page text was cut from the LLM observation."""
import json
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_bl_"))

from aap.graphs.browser import _compact_obs, _plan_tab_urls, offline_plan  # noqa: E402


def test_generic_tab_requests_need_no_llm():
    # ctx=None: generic requests must return before any LLM call (live case 5.13 regression guard)
    for g in ("open 5 tabs in chrome", "i want u to open 5 tabs of youtube in chrome"):
        assert _plan_tab_urls(None, g, 5, offline_plan(g)) == []


def test_compact_obs_keeps_text_and_fits_budget():
    obs = {"url": "https://www.youtube.com/results?search_query=segment+tree", "title": "segment tree - YouTube",
           "tabs": ["a", "b"], "text": "Segment Tree | Range Queries - video title one " * 40,
           "elements": [{"ref": str(i), "role": "link", "name": "x" * 120} for i in range(400)]}
    s = _compact_obs(obs)
    d = json.loads(s)
    assert len(s) <= 9000
    assert "Segment Tree" in d["text"] and d["url"].startswith("https://www.youtube.com")
    assert 0 < len(d["elements"]) < 400
