"""Bridge security tests — never launches Chrome or touches the desktop (confirm/approved/act are stubbed)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridge import desktop as desktop_mod
from bridge import security
from bridge.forwarder import client_allowed, rewrite_request_head, rewrite_response
from bridge.server import create_app

TOKEN = "t" * 43


def make(desktop=False, chrome=False, confirm=lambda *a: False, approved=lambda *a: False):
    return TestClient(create_app(TOKEN, desktop=desktop, chrome_enabled=chrome, confirm=confirm,
                                 approved=approved, start_forwarder=False))


H = {"X-Bridge-Token": TOKEN}


# -- token ---------------------------------------------------------------------------------------
def test_missing_token_rejected():
    assert make().get("/health").status_code == 401


def test_bad_token_rejected():
    assert make().get("/health", headers={"X-Bridge-Token": "nope"}).status_code == 401


def test_good_token_and_features_off_by_default():
    r = make().get("/health", headers=H)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "features": {"desktop": False, "chrome": False}}


def test_token_ok_helper():
    assert security.token_ok("abc", "abc")
    assert not security.token_ok("abc", "abd")
    assert not security.token_ok("abc", None)
    assert not security.token_ok("abc", "")


def test_token_file_created_and_stable(tmp_path: Path):
    p = tmp_path / "tok"
    t1 = security.load_or_create_token(path=p)
    assert len(t1) >= 32 and security.load_or_create_token(path=p) == t1
    assert security.load_or_create_token(rotate=True, path=p) != t1


# -- feature gates ---------------------------------------------------------------------------------
def test_desktop_disabled_returns_403():
    c = make()
    assert c.get("/desktop/screenshot", headers=H).status_code == 403
    assert c.post("/desktop/act", json={"action": "click", "x": 1, "y": 1}, headers=H).status_code == 403


def test_chrome_disabled_returns_403():
    assert make().post("/chrome/launch", json={}, headers=H).status_code == 403


# -- profile path policy ---------------------------------------------------------------------------
def test_default_chrome_profile_refused(tmp_path: Path):
    protected = [tmp_path / "Google" / "Chrome" / "User Data"]
    with pytest.raises(security.ProfileRefused):
        security.resolve_profile_dir(str(protected[0]), None, protected=protected)
    with pytest.raises(security.ProfileRefused):
        security.resolve_profile_dir(str(protected[0] / "Default"), None, protected=protected)
    with pytest.raises(security.ProfileRefused):  # case-insensitive on Windows paths
        security.resolve_profile_dir(str(protected[0]).upper() if Path("C:/").exists() else str(protected[0]),
                                     None, protected=protected)


def test_dedicated_profile_allowed(tmp_path: Path):
    protected = [tmp_path / "Google" / "Chrome" / "User Data"]
    ok = security.resolve_profile_dir(str(tmp_path / "agent-profile"), None, protected=protected)
    assert ok == tmp_path / "agent-profile"
    # sibling with a common prefix is NOT inside the protected dir
    sib = security.resolve_profile_dir(str(tmp_path / "Google" / "Chrome" / "User Data2"), None, protected=protected)
    assert sib.name == "User Data2"


def test_default_name_sanitised(tmp_path: Path):
    p = security.resolve_profile_dir(None, "../../evil name", protected=[], base=tmp_path)
    assert p.parent == tmp_path and ".." not in p.name and "/" not in p.name


def test_real_default_dir_refused_via_api(monkeypatch, tmp_path: Path):
    real = security.default_chrome_dirs()[0]
    called = []
    monkeypatch.setattr("bridge.chrome.launch", lambda *a, **k: called.append(a) or {})
    r = make(chrome=True).post("/chrome/launch", json={"profile_dir": str(real)}, headers=H)
    assert r.status_code == 400 and "refusing" in r.json()["detail"]
    assert not called


def test_chrome_launch_uses_dedicated_dir(monkeypatch, tmp_path: Path):
    seen = {}
    monkeypatch.setattr("bridge.chrome.launch", lambda udd, name=None: seen.update(udd=udd) or {"already_running": False})
    r = make(chrome=True).post("/chrome/launch", json={"profile_dir": str(tmp_path / "p1")}, headers=H)
    assert r.status_code == 200
    assert r.json()["cdp_url"] == "http://host.docker.internal:9223"
    assert seen["udd"] == tmp_path / "p1"


# -- approval gating ------------------------------------------------------------------------------
def test_desktop_act_denied_without_approval(monkeypatch):
    acted = []
    monkeypatch.setattr(desktop_mod, "act", lambda **k: acted.append(k) or {"ok": True})
    c = make(desktop=True, confirm=lambda *a: False, approved=lambda *a: False)
    r = c.post("/desktop/act", json={"action": "click", "x": 5, "y": 5}, headers=H)
    assert r.status_code == 403 and not acted


def test_desktop_act_allowed_by_gateway_approval(monkeypatch):
    acted, asked = [], []
    monkeypatch.setattr(desktop_mod, "act", lambda **k: acted.append(k) or {"ok": True})
    c = make(desktop=True, confirm=lambda *a: asked.append(a) or False,
             approved=lambda url, aid: aid == "ap1")
    r = c.post("/desktop/act", json={"action": "hotkey", "keys": ["ctrl", "s"]},
               headers={**H, "X-Approval-Id": "ap1"})
    assert r.status_code == 200 and acted and not asked


def test_desktop_act_unknown_approval_falls_back_to_local_confirm(monkeypatch):
    acted, asked = [], []
    monkeypatch.setattr(desktop_mod, "act", lambda **k: acted.append(k) or {"ok": True})
    c = make(desktop=True, confirm=lambda *a: asked.append(a) or True, approved=lambda *a: False)
    r = c.post("/desktop/act", json={"action": "type", "text": "hi"}, headers={**H, "X-Approval-Id": "bogus"})
    assert r.status_code == 200 and asked and acted


# -- forwarder helpers ----------------------------------------------------------------------------
def test_client_allowlist():
    import ipaddress
    nets = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("172.20.0.0/20")]
    assert client_allowed("127.0.0.1", nets)
    assert client_allowed("172.20.5.9", nets)
    assert not client_allowed("192.168.1.50", nets)
    assert not client_allowed("garbage", nets)


def test_host_header_rewrite_and_body_fix():
    head = b"GET /json/version HTTP/1.1\r\nHost: host.docker.internal:9223\r\nAccept: */*\r\n\r\n"
    new, orig = rewrite_request_head(head, "localhost:9222")
    assert b"Host: localhost:9222\r\n" in new and orig == "host.docker.internal:9223"
    body = b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/x"}'
    resp = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    out = rewrite_response(resp, "localhost:9222", orig)
    head2, body2 = out.split(b"\r\n\r\n", 1)
    assert b"ws://host.docker.internal:9223/devtools" in body2
    assert f"Content-Length: {len(body2)}".encode() in head2
