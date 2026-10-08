"""Browser service smoke test (headless, local page only). Skipped when Playwright/Chromium is unavailable."""
import base64
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("playwright")

PAGE = """<html><head><title>AOS test</title></head><body>
<input id="q" placeholder="Search here"><button id="go" onclick="document.getElementById('out').innerText='clicked:'+document.getElementById('q').value">Go</button>
<div id="out"></div><a href="#x">A link</a></body></html>"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("BROWSER_HEADLESS", "true")
    import importlib
    import browser_svc.app as mod
    importlib.reload(mod)
    from fastapi.testclient import TestClient
    with TestClient(mod.app) as c:
        yield c


def test_session_snapshot_act_takeover(client):
    try:
        sid = client.post("/session", json={"profile": "test"}).json()["session_id"]
    except Exception as e:  # chromium not installed
        pytest.skip(f"chromium unavailable: {e}")
    url = "data:text/html;base64," + base64.b64encode(PAGE.encode()).decode()
    assert client.post("/act", json={"session_id": sid, "action": "goto", "url": url}).json()["ok"]
    snap = client.get("/snapshot", params={"session_id": sid}).json()
    assert snap["title"] == "AOS test"
    by_name = {e["name"] or e["text"]: e["ref"] for e in snap["elements"]}
    assert "Search here" in by_name and "Go" in by_name
    r = client.post("/act", json={"session_id": sid, "action": "type", "ref": by_name["Search here"], "text": "hello"})
    assert r.json()["ok"], r.json()
    assert client.post("/act", json={"session_id": sid, "action": "click", "ref": by_name["Go"]}).json()["ok"]
    assert "clicked:hello" in client.get("/snapshot", params={"session_id": sid}).json()["text"]
    shot = client.get("/screenshot", params={"session_id": sid})
    assert shot.headers["content-type"] == "image/png" and shot.content[:4] == b"\x89PNG"
    assert client.post("/takeover", json={"on": True}).json()["takeover"] is True
    assert client.post("/act", json={"session_id": sid, "action": "wait", "ms": 10}).status_code == 423
    client.post("/takeover", json={"on": False})
    assert client.post("/session", json={"profile": "../evil"}).status_code == 400
    assert "test" in client.get("/profiles").json()["profiles"]
