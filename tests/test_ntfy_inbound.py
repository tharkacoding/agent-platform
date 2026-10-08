"""Phone (ntfy) → AgentOS / Claude Code routing, offline."""
import json
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_ntfy_"))

from aap import events, registry  # noqa: E402
from aap.channels import ntfy  # noqa: E402

registry.init()


def _line(text, tags=None, mid="m1"):
    return json.dumps({"id": mid, "event": "message", "message": text, "tags": tags or []})


def test_claude_prefix_goes_to_inbox_not_agentos(monkeypatch):
    started = []
    monkeypatch.setattr("aap.core.runtime.runtime.start_chat", lambda *a, **k: started.append(a) or ("r", "c"))
    f = ntfy.inbox_file()
    if f.exists():
        f.unlink()
    out = ntfy.Ntfy.handle_line(_line("Claude, why did C2 fail?", mid="a1"))
    assert out["action"] == "claude" and not started
    item = json.loads(f.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert item["text"] == "why did C2 fail?" and item["source"] == "ntfy"
    assert json.loads(ntfy._state_file().read_text())["last_id"] == "a1"


def test_own_pushes_are_ignored(monkeypatch):
    started = []
    monkeypatch.setattr("aap.core.runtime.runtime.start_chat", lambda *a, **k: started.append(a) or ("r", "c"))
    assert ntfy.Ntfy.handle_line(_line("AgentOS: Done", tags=["white_check_mark", "aos-out"], mid="a2")) is None
    assert not started
    assert ntfy.Ntfy.handle_line(json.dumps({"id": "k", "event": "keepalive"})) is None


def test_plain_text_starts_a_phone_task_with_push(monkeypatch):
    calls = []
    monkeypatch.setattr("aap.core.runtime.runtime.start_chat",
                        lambda text, cid, **k: calls.append((text, cid, k)) or ("run-p1", cid))
    out = ntfy.Ntfy.handle_line(_line("what is 2+2", mid="a3"))
    assert out == {"action": "task", "run_id": "run-p1"}
    text, cid, kw = calls[-1]
    assert text == "what is 2+2" and cid and kw.get("notify") is True
    st = json.loads(ntfy._state_file().read_text())
    assert st["phone_run"] == "run-p1" and st["phone_cid"] == cid


def test_message_while_phone_task_runs_changes_course(monkeypatch):
    run = events.create("run-p2")
    st = {"phone_run": "run-p2", "phone_cid": "c"}
    out = ntfy.route("make it shorter", st)
    assert out["action"] == "steer" and run.steer and run.steer[-1]["text"] == "make it shorter"


def test_yes_decides_pending_approval_of_phone_task(monkeypatch):
    events.create("run-p3")
    decided = []
    monkeypatch.setattr("aap.core.approvals.pending", lambda: [{"id": "ap1", "run_id": "run-p3", "title": "delete"}])
    monkeypatch.setattr("aap.core.approvals.decide", lambda aid, d, *a: decided.append((aid, d)) or True)
    assert ntfy.route("no", {"phone_run": "run-p3"})["action"] == "approval"
    assert decided == [("ap1", "deny")]


def test_text_answers_pending_question_card(monkeypatch):
    events.create("run-p4")
    got = []
    monkeypatch.setattr("aap.core.approvals.pending", lambda: [])
    monkeypatch.setattr("aap.core.questions.pending",
                        lambda rid="": [{"id": "q1", "questions": [{"q": "which?", "default": "A"},
                                                                   {"q": "lang?", "default": "Python"}]}])
    monkeypatch.setattr("aap.core.questions.answer", lambda qid, ans: got.append((qid, ans)) or True)
    out = ntfy.route("Two Sum", {"phone_run": "run-p4"})
    assert out["action"] == "answer" and got == [("q1", ["Two Sum", "Python"])]


def test_question_to_running_task_gets_status_not_new_course(monkeypatch):
    run = events.create("run-p5")
    run.emit("node", "browser_agent", "running", "Opening YouTube")
    pushed = []
    monkeypatch.setattr("aap.notify.screen_png", lambda: b"")
    monkeypatch.setattr("aap.channels.ntfy._push", lambda t, b: pushed.append(b))
    out = ntfy.route("Where are you opening YouTube?", {"phone_run": "run-p5"})
    assert out["action"] == "status" and not run.steer and "Opening YouTube" in pushed[-1]
    assert ntfy.route("stop", {"phone_run": "run-p5"})["action"] == "stop" and run.cancelled


def test_screenshot_command(monkeypatch):
    sent = []
    monkeypatch.setattr("aap.notify.screen_png", lambda: b"PNG")
    monkeypatch.setattr("aap.notify.send_image", lambda *a, **k: sent.append(a) or True)
    assert ntfy.route("send me a screenshot", {})["action"] == "screenshot" and sent


def test_profile_only_if_owner_named_it(monkeypatch):
    from aap.tools import desktop
    rid = registry.new_run("chat", "open youtube on my laptop and play a song")
    class C:
        run = events.create(rid)
    assert not desktop._profile_requested(C, "asdf")
    rid2 = registry.new_run("chat", "open my asdf chrome profile and submit leetcode")
    class D:
        run = events.create(rid2)
    assert desktop._profile_requested(D, "asdf") and not desktop._profile_requested(D, "Challa")


def test_agentos_default_profile_is_not_a_real_profile():
    from aap.tools.browser import _is_real_profile
    assert _is_real_profile("default") is False
