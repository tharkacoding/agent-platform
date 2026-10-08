"""§10 extension gateway, §11 real Chrome profile import, §12 site memory — no real Chrome, no real profile."""
import json
import os
import tempfile
import threading
import time

os.environ.setdefault("AAP_LLM_PROVIDER", "mock")
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_future_"))

import pytest  # noqa: E402


# ------------------------------------------------------------------------------------------- §11 profiles
def _fake_user_data(tmp_path):
    ud = tmp_path / "User Data"
    for d in ("Default", "Profile 1"):
        (ud / d / "Network").mkdir(parents=True)
        (ud / d / "Network" / "Cookies").write_bytes(b"cookie-db-" + d.encode())
        (ud / d / "Login Data").write_bytes(b"logins")
        (ud / d / "Preferences").write_text("{}")
        (ud / d / "Local Storage" / "leveldb").mkdir(parents=True)
        (ud / d / "Local Storage" / "leveldb" / "000003.log").write_text("ls")
        (ud / d / "History").write_text("not copied")
    (ud / "Local State").write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Person 1", "user_name": "me@example.com"},
        "Profile 1": {"name": "Work", "user_name": "work@example.com"},
        "Profile 9": {"name": "Ghost"}}}}))     # listed but folder missing → skipped
    return ud


def test_list_profiles(tmp_path):
    from bridge import profiles
    ud = _fake_user_data(tmp_path)
    r = profiles.list_profiles(ud, running=lambda: False)
    assert [p["dir"] for p in r["profiles"]] == ["Default", "Profile 1"]
    assert r["profiles"][1]["name"] == "Work" and r["in_use"] is False
    assert profiles.list_profiles(tmp_path / "nope")["profiles"] == []


def test_import_refused_while_chrome_runs(tmp_path):
    from bridge import profiles
    ud = _fake_user_data(tmp_path)
    (ud / "lockfile").write_text("")
    with pytest.raises(profiles.ImportRefused, match="close ALL Chrome"):
        profiles.import_profile("Profile 1", "work", user_data=ud, dest_base=tmp_path / "dest", running=lambda: True)
    with pytest.raises(profiles.ImportRefused):
        profiles.import_profile("..\\x", "bad", user_data=ud, dest_base=tmp_path / "dest", running=lambda: False)
    with pytest.raises(profiles.ImportRefused):
        profiles.import_profile("Profile 7", "missing", user_data=ud, dest_base=tmp_path / "dest", running=lambda: False)


def test_import_copies_login_files_only(tmp_path):
    from bridge import profiles
    ud = _fake_user_data(tmp_path)
    before = {p: p.read_bytes() for p in ud.rglob("*") if p.is_file()}
    r = profiles.import_profile("Profile 1", "work stuff", user_data=ud, dest_base=tmp_path / "dest",
                                running=lambda: False)
    dest = tmp_path / "dest" / "work-stuff"
    assert r["ok"] and r["name"] == "work-stuff"
    assert (dest / "Local State").is_file()
    assert (dest / "Default" / "Network" / "Cookies").read_bytes() == b"cookie-db-Profile 1"
    assert (dest / "Default" / "Local Storage" / "leveldb" / "000003.log").is_file()
    assert not (dest / "Default" / "History").exists()                       # history/caches are not copied
    assert {p: p.read_bytes() for p in ud.rglob("*") if p.is_file()} == before   # source untouched


def test_import_dest_never_inside_chrome_dirs(tmp_path, monkeypatch):
    from bridge import profiles, security
    ud = _fake_user_data(tmp_path)
    monkeypatch.setattr(security, "default_chrome_dirs", lambda: [ud])
    with pytest.raises(security.ProfileRefused):
        profiles.import_profile("Default", "x", user_data=ud, dest_base=ud, running=lambda: False)


# ------------------------------------------------------------------------------------------- §12 site memory
@pytest.fixture()
def sm(tmp_path, monkeypatch):
    from aap import site_memory
    monkeypatch.setattr(site_memory, "DIR", tmp_path / "sm")
    (tmp_path / "sm").mkdir()
    site_memory._cache.clear()
    return site_memory


def test_site_memory_record_and_hint(sm):
    hist = [{"action": {"action": "goto", "url": "https://www.leetcode.com/problemset/"},
             "result": {"ok": True, "url": "https://leetcode.com/problemset/"}},
            {"action": {"action": "click", "ref": "12", "ref_name": "Two Sum", "role": "link"},
             "result": {"ok": True, "url": "https://leetcode.com/problems/two-sum/"}},
            {"action": {"action": "click", "ref": "3", "ref_name": "Broken"}, "result": {"error": "not found"}}]
    for _ in range(3):
        sm.record_run("open leetcode problem 1", hist, [{"url": "https://leetcode.com/problems/two-sum/",
                                                         "title": "Two Sum"}])
    d = sm.load("leetcode.com")
    assert d["visits"] == 3 and d["elements"][0]["name"] == "Two Sum" and d["elements"][0]["uses"] == 3
    assert all(e["name"] != "Broken" for e in d["elements"])               # failed actions are not learned
    assert "/problems/*" in d["pages"]
    h = sm.hint("https://leetcode.com/problems/x/", "open leetcode problem 7")
    assert "leetcode.com" in h and "Two Sum" in h and "worked via" in h and len(h) <= 400
    assert sm.list_sites()[0]["known"] is True
    assert sm.delete("leetcode.com") and sm.hint("https://leetcode.com/") == ""


def test_snapshot_cache_ttl(sm):
    sm.cache_put("https://news.ycombinator.com/", {"url": "x", "elements": []})
    sm.cache_put("https://docs.python.org/3/", {"url": "y", "elements": []})
    now = time.time()
    assert sm.cache_get("https://news.ycombinator.com/", now + 60) is not None
    assert sm.cache_get("https://news.ycombinator.com/", now + 700) is None      # news: 10 min
    assert sm.cache_get("https://docs.python.org/3/", now + 700) is not None     # default: 1 h
    assert sm.cache_get("https://docs.python.org/3/", now + 4000) is None
    sm.cache_put("https://a.com/", {"error": "boom"})
    assert sm.cache_get("https://a.com/") is None


# ------------------------------------------------------------------------------------------- §10 extension
def test_extension_socket_roundtrip():
    from fastapi.testclient import TestClient
    from aap import extension
    from aap.api.app import app
    client = TestClient(app)
    tok = extension.token()
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/extension?token=wrong") as ws:
            ws.receive_text()
    with client.websocket_connect(f"/ws/extension?token={tok}") as ws:
        ws.send_text(json.dumps({"type": "hello", "version": "0.1.0", "browser": "test"}))
        assert json.loads(ws.receive_text())["type"] == "config"
        ws.send_text(json.dumps({"type": "tabs", "tabs": [{"id": 7, "url": "https://example.com/", "title": "Ex",
                                                           "active": True}]}))
        out = {}
        t = threading.Thread(target=lambda: out.update(r=extension.hub.call("tabs.list", timeout=10)))
        t.start()
        req = json.loads(ws.receive_text())
        assert req["cmd"] == "tabs.list"
        ws.send_text(json.dumps({"id": req["id"], "ok": True, "result": {"tabs": [{"id": 7}]}}))
        t.join(10)
        assert out["r"] == {"tabs": [{"id": 7}]}
        st = client.get("/api/extension/status").json()
        assert st["connected"] and st["tabs"][0]["id"] == 7
    time.sleep(0.2)
    assert extension.hub.connected is False
    with pytest.raises(extension.ExtensionUnavailable):
        extension.hub.call("tabs.list", timeout=1)


def test_extension_allowlist_and_mode():
    from aap import extension, settings
    from aap.tools.browser import resolve_mode
    settings.update({"extension_allowlist": "leetcode.com, github.com"})
    assert extension.site_allowed("https://leetcode.com/problems/x") and extension.site_allowed("https://api.github.com/")
    assert not extension.site_allowed("https://bank.example.com/")
    assert resolve_mode("extension", "host") == "extension"     # explicitly asked
    assert resolve_mode("extension", "sandbox") == "sandbox"    # agent without real-browser permission
    settings.update({"browser_default_mode": "extension"})
    assert resolve_mode("host", "host") == "host"               # extension not connected → AgentOS Chrome profile
    settings.update({"browser_default_mode": "sandbox", "extension_allowlist": ""})
