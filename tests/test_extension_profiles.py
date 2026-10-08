"""N9: one AgentOS extension per Chrome profile — several connected at once, per-profile 'wanted' switch (default off)
and allowlist, browser_open routing to a wanted+connected profile, chrome_extension_profiles tool, pre-paired VM loading."""
import asyncio
import json
import os
import tempfile
import threading

import pytest

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_extprof_"))

from aap import extension, settings  # noqa: E402
from aap.tools import browser as tb  # noqa: E402


class FakeSocket:
    def __init__(self, name):
        self.name, self.sent, self.closed = name, [], None

    async def send_text(self, t):
        self.sent.append(json.loads(t))
        m = self.sent[-1]
        if "cmd" in m and self.hub:   # act like an extension: answer straight away
            reply = {"id": m["id"], "ok": True, "result": {"from": self.name, "cmd": m["cmd"], "tabs": [
                {"id": 7, "url": "https://leetcode.com/", "active": True}] if m["cmd"] == "tabs.list" else None,
                "id": 9, "url": "about:blank"}}
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.hub.on_message(reply, self)))

    async def close(self, code=None):
        self.closed = code

    hub = None


@pytest.fixture
def hub():
    h = extension.Hub()
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    h.loop = loop

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(10)
    h.run = run
    old = extension.hub
    extension.hub = h
    settings.update({"extension_profiles": {}, "extension_allowlist": "github.com"})
    yield h
    extension.hub = old
    loop.call_soon_threadsafe(loop.stop)


def connect(h, name, email=""):
    s = FakeSocket(name)
    s.hub = h
    h.run(h.attach(s, name))
    h.run(h.on_message({"type": "hello", "version": "0.2.0", "email": email}, s))
    return s


def test_several_profiles_connect_at_once_and_default_to_off(hub):
    a, b = connect(hub, "asdf", "a@x.com"), connect(hub, "Challa")
    st = {p["key"]: p for p in extension.profiles_status()}
    assert set(st) == {"asdf", "challa"} and st["asdf"]["email"] == "a@x.com"
    assert not st["asdf"]["wanted"] and not extension.ready("asdf")          # default OFF
    with pytest.raises(extension.ExtensionUnavailable, match="not allowed"):
        hub.call("tabs.list", profile="asdf")                                # connected but not wanted
    extension.set_profile("asdf", wanted_=True)
    assert extension.ready("asdf") and not extension.ready("challa")
    assert hub.call("tabs.list", profile="ASDF", timeout=5)["from"] == "asdf"   # case-insensitive, right socket
    assert [m for m in b.sent if "cmd" in m] == []                           # the other profile was never touched
    with pytest.raises(extension.ExtensionUnavailable, match="not connected"):
        hub.call("tabs.list", profile="nobody")


def test_reconnect_replaces_and_detach_only_drops_that_profile(hub):
    a1 = connect(hub, "asdf")
    a2 = connect(hub, "asdf")
    assert a1.closed == 4000 and len(hub.conns) == 1
    b = connect(hub, "om")
    hub.run(hub.detach(a2))
    assert set(hub.conns) == {"om"} and hub.connected and hub.socket is b       # legacy attrs follow the remaining one
    hub.run(hub.detach(b))
    assert not hub.connected


def test_per_profile_allowlist_adds_to_global(hub):
    extension.set_profile("asdf", wanted_=True, allow="leetcode.com, codeforces.com")
    assert extension.site_allowed("https://leetcode.com/x", "asdf")
    assert extension.site_allowed("https://api.github.com/", "asdf")        # global still applies
    assert not extension.site_allowed("https://leetcode.com/x", "om")       # another profile: not allowed there
    assert not extension.site_allowed("https://leetcode.com/x")             # no profile → global only
    s = connect(hub, "asdf")
    cfg = [m for m in s.sent if m.get("type") == "config"][-1]
    assert "leetcode.com" in cfg["allowlist"] and "github.com" in cfg["allowlist"] and cfg["wanted"] is True


class Ctx:
    class _run:
        run_id = "r-ext"

    class _task:
        task_id = "s1"
        parent_agent = ""
        instruction = "open leetcode in profile asdf"
        context = []

    class _perm:
        browser = "host"

    class _spec:
        id = "browser_agent"
        tools = ["browser_open"]
        permissions = None

    def __init__(self, browser="host"):
        self.run, self.task = self._run(), self._task()
        self.spec = self._spec()
        self.spec.permissions = type("P", (), {"browser": browser})()
        self.approved, self.events = [], []

    def approve(self, title, detail=None):
        self.approved.append(title)

    def emit(self, *a, **k):
        self.events.append(a)


def test_browser_open_routes_to_wanted_connected_profile(hub):
    connect(hub, "asdf")
    ctx = Ctx()
    r = tb.browser_open(ctx, profile="asdf")                                # connected but NOT wanted → not the extension
    assert r.get("mode") != "extension" and not ctx.approved
    tb.close_run("r-ext")                                                      # drop the session the first call made
    extension.set_profile("asdf", wanted_=True)
    ctx = Ctx()
    r = tb.browser_open(ctx, profile="asdf")
    assert r["mode"] == "extension" and r["profile"] == "asdf" and ctx.approved   # approval as for any real profile
    sess = tb._SESSIONS["r-ext:s1"]
    assert sess["ext_profile"] == "asdf" and sess["tab_id"] == 9               # fresh tab, not the owner's tab
    assert tb._ext_call(sess, "tabs.list", {})["from"] == "asdf"
    sandbox_only = Ctx(browser="sandbox")                                      # agent without real-browser permission
    sandbox_only.run.run_id = "r-ext2"
    assert tb.browser_open(sandbox_only, profile="asdf").get("mode") != "extension"
    tb.close_run("r-ext")


def test_write_actions_use_the_profiles_own_allowlist(hub):
    connect(hub, "asdf")
    extension.set_profile("asdf", wanted_=True, allow="leetcode.com")
    hub.conn("asdf").tabs = [{"id": 7, "url": "https://leetcode.com/problems/x"}]
    ctx = Ctx()
    sess = {"session_id": "ext", "mode": "extension", "tab_id": 7, "profile": "asdf", "ext_profile": "asdf"}
    tb._ext_act(ctx, sess, "click", {"ref": "3"})
    assert ctx.approved == []                                                  # allowlisted for THIS profile → no prompt
    hub.conn("asdf").tabs = [{"id": 7, "url": "https://bank.example.com/"}]
    tb._ext_act(ctx, sess, "click", {"ref": "3"})
    assert ctx.approved and "bank.example.com" in ctx.approved[0]


def test_chrome_extension_profiles_tool(hub):
    from aap.core.sdk import TOOLS, UNIVERSAL_TOOLS
    connect(hub, "asdf", "a@x.com")
    connect(hub, "om")
    extension.set_profile("asdf", wanted_=True)
    out = TOOLS["chrome_extension_profiles"].fn(object())
    assert out["usable"] == ["asdf"] and {p["profile"] for p in out["profiles"]} == {"asdf", "om"}
    assert "chrome_extension_profiles" in UNIVERSAL_TOOLS


def test_status_and_routes(hub):
    from fastapi.testclient import TestClient

    from aap.api.app import app
    client = TestClient(app)
    connect(hub, "asdf")
    r = client.put("/api/extension/profile", json={"profile": "asdf", "wanted": True, "allowlist": "leetcode.com"}).json()
    p = next(x for x in r["profiles"] if x["key"] == "asdf")
    assert p["wanted"] and p["allowlist"] == ["leetcode.com"] and p["connected"]
    assert client.put("/api/extension/profile", json={"profile": " "}).status_code == 400


def test_vm_login_window_is_prepaired_with_the_extension(tmp_path, monkeypatch):
    import browser_svc.app as bsvc
    monkeypatch.setattr(bsvc, "PROFILES_DIR", tmp_path / "browser_profiles")
    d = bsvc._prepare_extension("asdf", "tok123", "ws://127.0.0.1:7870/ws/extension")
    cfg = json.loads((tmp_path / "extensions" / "asdf" / "config.json").read_text(encoding="utf-8"))
    assert d.endswith("asdf") and cfg == {"token": "tok123", "url": "ws://127.0.0.1:7870/ws/extension", "profile": "asdf"}
    assert (tmp_path / "extensions" / "asdf" / "manifest.json").is_file()
    assert not (tmp_path / "extensions" / "asdf" / "README.md").exists()
