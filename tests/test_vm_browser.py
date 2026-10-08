"""M7: web work in a profile logged in inside the test VM's browser runs there (browser_agent), never on the screen."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_vm_"))

from aap import settings, vmbrowser  # noqa: E402
from aap.graphs.main import _prefer_vm_browser  # noqa: E402
from aap.tools import browser as tb  # noqa: E402


def _vm(monkeypatch, profiles=("asdf",)):
    monkeypatch.setattr(settings, "get", lambda k, d=None: {"vm_browser_url": "http://vm:8012"}.get(k, d))
    monkeypatch.setattr(vmbrowser, "profiles", lambda refresh=False: set(profiles))


def test_desktop_web_step_moves_to_vm_browser(monkeypatch):
    _vm(monkeypatch)
    st = _prefer_vm_browser({"id": "s1", "agent_id": "desktop_agent",
                             "instruction": "open my asdf chrome profile, go to leetcode.com and submit the code"})
    assert st["agent_id"] == "browser_agent" and "VM browser" in st["instruction"]


def test_desktop_app_and_unknown_profile_stay(monkeypatch):
    _vm(monkeypatch)
    import aap.clients as cl
    monkeypatch.setattr(cl, "service_health", lambda: {"bridge": True})      # a machine WITH desktop control
    app = {"id": "s1", "agent_id": "desktop_agent", "instruction": "in profile asdf open notepad and type hi"}
    assert _prefer_vm_browser(app)["agent_id"] == "desktop_agent"
    other = {"id": "s1", "agent_id": "desktop_agent", "instruction": "open my Challa profile and go to github.com"}
    assert _prefer_vm_browser(other)["agent_id"] == "desktop_agent"


def test_vm_profile_is_not_a_real_profile(monkeypatch):
    _vm(monkeypatch)
    assert tb._is_real_profile("asdf") is False


def test_vm_session_uses_vm_service(monkeypatch):
    _vm(monkeypatch)
    calls = []

    def fake(method, path, **kw):
        calls.append((method, path, kw.get("json")))
        return {"session_id": "vm1", "url": "about:blank"} if path == "/session" else {"ok": True}
    monkeypatch.setattr(vmbrowser, "call", fake)

    class Run:
        run_id = "r-vm"

    class Task:
        task_id = "s1"

    class Ctx:
        run, task = Run(), Task()
        def emit(self, *a, **k): pass
    r = tb._vm_open(Ctx(), "asdf")
    assert r["mode"] == "vm" and tb._SESSIONS["r-vm:s1"]["svc"] == "vm"
    tb._svc(tb._SESSIONS["r-vm:s1"], "POST", "/act", json={"action": "goto"})
    assert calls[-1][1] == "/act"
    tb.close_run("r-vm")
    assert calls[-1][1] == "/session/close"


def test_workspace_file_opens_as_real_file_url(monkeypatch):
    import aap.clients as cl
    seen = []
    monkeypatch.setattr(cl, "runner", lambda m, p, **k: seen.append(k["params"]["path"]) or
                        {"file_url": "file:///opt/agentos-data/workspace/site/index.html"})
    assert tb._local_file_url("/workspace/site/index.html") == "file:///opt/agentos-data/workspace/site/index.html"
    assert tb._local_file_url("file:///workspace/site/index.html").startswith("file:///opt/")
    assert seen == ["/workspace/site/index.html", "/workspace/site/index.html"]
    assert tb._local_file_url("https://example.com/workspace/x") == "https://example.com/workspace/x"


def test_site_scoped_standing_preference():
    from aap.core.execstate import standing_site
    assert standing_site("always use my asdf profile for leetcode") == "leetcode"
    assert standing_site("from now on use profile asdf on codeforces.com") == "codeforces"
    assert standing_site("always use my asdf profile") == ""
    assert standing_site("always use my asdf profile for everything") == ""


def test_site_profile_only_applies_to_that_site(monkeypatch):
    vals = {"site_profiles": {"leetcode": "asdf"}, "browser_profile": "default"}
    monkeypatch.setattr(settings, "get", lambda k, d=None: vals.get(k, d))

    class T:
        context, parent_agent = [], ""

        def __init__(self, i): self.instruction = i

    class C:
        def __init__(self, i): self.task = T(i)
    assert tb.resolve_profile(C("solve two sum on leetcode"))[0] == "asdf"
    assert tb.resolve_profile(C("play songs on youtube"))[0] == "default"



def test_each_step_has_its_own_session_and_releases_it(monkeypatch):
    calls = []
    monkeypatch.setattr(tb, "_svc", lambda sess, m, path, **k: calls.append((path, sess["session_id"])) or {"ok": True})

    class Run:
        run_id = "r-par"

    def ctx(tid):
        class T:
            task_id = tid

        class C:
            run, task = Run(), T()
        return C()
    tb._put(ctx("s1"), {"session_id": "A", "mode": "sandbox", "profile": "asdf"})
    tb._put(ctx("s2"), {"session_id": "B", "mode": "sandbox", "profile": "asdf"})
    assert tb._SESSIONS["r-par:s1"]["session_id"] != tb._SESSIONS["r-par:s2"]["session_id"]   # parallel: own tabs
    tb.release_step("r-par", "s1")
    assert ("/session/release", "A") in calls and "r-par:s1" not in tb._SESSIONS
    tb.close_run("r-par")
    assert {("/session/close", "A"), ("/session/close", "B")} <= set(calls) and "r-par:s2" not in tb._SESSIONS



def test_profile_from_request_and_no_desktop_machine(monkeypatch):
    _vm(monkeypatch)
    import aap.clients as cl
    step = {"id": "s3", "agent_id": "desktop_agent",
            "instruction": "On the LeetCode Two Sum page already open from s2, paste the code and click Submit"}
    monkeypatch.setattr(cl, "service_health", lambda: {"bridge": True})
    out = _prefer_vm_browser(step, "open a leetcode problem in my asdf profile and get it accepted")
    assert out["agent_id"] == "browser_agent" and "asdf" in out["instruction"]     # profile taken from the request
    monkeypatch.setattr(cl, "service_health", lambda: {"bridge": False})          # Linux VM: no desktop at all
    other = {"id": "s1", "agent_id": "desktop_agent", "instruction": "open github.com and check the login"}
    assert _prefer_vm_browser(other, "check github")["agent_id"] == "browser_agent"
