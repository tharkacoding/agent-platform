"""Host-Chrome mode: settings/mode resolution, offline goal parser, bridge CDP URL selection. No real Chrome."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_bh_"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from aap import settings  # noqa: E402
from aap.graphs.browser import offline_plan, requested_mode  # noqa: E402
from aap.tools.browser import _open_risk, resolve_mode  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    settings.update({"browser_default_mode": "sandbox", "browser_profile": "default"})
    yield
    settings.update({"browser_default_mode": "sandbox"})


def test_mode_defaults_to_settings():
    assert resolve_mode("", "host") == "sandbox"
    settings.update({"browser_default_mode": "host"})
    assert resolve_mode("", "host") == "host"
    assert resolve_mode("", "sandbox") == "sandbox"        # agent without host permission never gets host
    assert resolve_mode("sandbox", "host") == "sandbox"    # explicit request wins
    assert resolve_mode("bogus", "host") == "sandbox"


def test_open_risk_only_for_host():
    assert _open_risk({"mode": "sandbox"}) is None
    assert _open_risk({"mode": "host"})
    settings.update({"browser_default_mode": "host"})
    assert _open_risk({"mode": ""})


def test_requested_mode_wording():
    assert requested_mode("open youtube in my chrome") == "host"
    assert requested_mode("use my own browser to check mail") == "host"
    assert requested_mode("in the sandbox open example.com") == "sandbox"
    assert requested_mode("open example.com") == ""


@pytest.mark.parametrize("goal,expected", [
    ("open https://example.com and take a screenshot", ["https://example.com"]),
    ("go to github.com", ["https://github.com"]),
    ("go to youtube", ["https://www.youtube.com"]),
    ("visit wikipedia", ["https://en.wikipedia.org"]),
    ("open codeforces", ["https://codeforces.com"]),
    ("search segment trees on youtube", ["https://www.youtube.com/results?search_query=segment+trees"]),
    ("search for best laptops 2026 on google", ["https://www.google.com/search?q=best+laptops+2026"]),
    ("search langgraph send api", ["https://duckduckgo.com/?q=langgraph+send+api"]),
    ("take a screenshot", []),
])
def test_offline_plan(goal, expected):
    assert offline_plan(goal) == expected


def test_settings_api_persists_browser_mode():
    from aap.api.app import app
    c = TestClient(app)
    r = c.post("/api/browser/profile", json={"mode": "host", "profile": "work stuff"})
    assert r.status_code == 200 and r.json()["mode"] == "host" and r.json()["active_profile"] == "work-stuff"
    assert settings.get("browser_default_mode") == "host"
    c.post("/api/browser/profile", json={"mode": "nonsense"})
    assert settings.get("browser_default_mode") == "host"


def test_bridge_cdp_url_local_vs_docker(monkeypatch, tmp_path):
    from bridge.server import create_app
    monkeypatch.setattr("bridge.chrome.launch", lambda udd, name=None: {"already_running": False})
    tok = "t" * 43
    c = TestClient(create_app(tok, chrome_enabled=True, start_forwarder=False))
    h = {"X-Bridge-Token": tok}
    body = {"profile_dir": str(tmp_path / "p")}
    assert c.post("/chrome/launch", json={**body, "cdp_public": "local"}, headers=h).json()["cdp_url"] == \
        "http://127.0.0.1:9222"
    assert c.post("/chrome/launch", json=body, headers=h).json()["cdp_url"] == "http://host.docker.internal:9223"
    monkeypatch.setenv("BRIDGE_CDP_PUBLIC", "local")
    assert c.post("/chrome/launch", json=body, headers=h).json()["cdp_url"] == "http://127.0.0.1:9222"
    assert c.post("/chrome/launch", json={**body, "cdp_public": "x"}, headers=h).status_code == 400
