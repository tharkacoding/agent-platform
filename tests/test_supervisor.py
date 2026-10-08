"""TODO N5: the Main Agent supervises running sub-agents at checkpoints (loops, ping-pong, wandering, no progress)."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_sup_"))

from aap.core import supervisor  # noqa: E402

LC = "https://leetcode.com/problems/valid-parentheses/"


def h(action, status="no_effect", **kw):
    return {"action": {"action": action, **kw}, "status": status}


def test_healthy_progress_has_no_signals():
    hist = [h("goto", "succeeded", url=LC), h("set_code", "succeeded", text="x"), h("click", "succeeded", ref="9"),
            h("wait_for", "succeeded", texts=["Accepted"])]
    assert supervisor.signals("submit valid parentheses on leetcode", hist) == []


def test_t1_pattern_is_caught():
    # replay of the real T1 run: Escape/menu clicks + reloads of the same two pages, nothing changing, then a stray site
    hist = [h("press", key="Escape"), h("goto", url=LC), h("click", ref="35"), h("goto", url=LC + "description/"),
            h("press", key="Escape"), h("goto", url=LC), h("click", ref="35"), h("goto", url=LC + "description/"),
            h("goto", url="https://www.in.com"), h("click", ref="35")]
    sig = " | ".join(supervisor.signals("open leetcode and submit valid parentheses " + LC, hist))
    assert "repeating" in sig and "back and forth" in sig and "unrelated" in sig and "no action" in sig


class _Run:
    run_id = "sup-run"

    def __init__(self):
        self.events = []

    def emit(self, *a, **k):
        self.events.append(a)


class _Ctx:
    def __init__(self):
        self.run = _Run()

        class T:
            task_id = "s1"

        class S:
            id = "browser_agent"
        self.task, self.spec = T(), S()

    def llm(self):
        from aap.llm import llm
        return llm


def test_checkpoint_redirects_then_hard_stops():
    ctx = _Ctx()
    stuck = [h("click", ref="35")] * 6
    v = supervisor.checkpoint(ctx, "submit on leetcode", stuck, 5)
    assert v["verdict"] == "redirect" and v["note"]            # mock model → the deterministic redirect fallback
    assert ctx.run.events and ctx.run.events[-1][0] == "supervisor"   # visible in the timeline as a Main Agent event
    assert supervisor.checkpoint(ctx, "submit on leetcode", stuck, supervisor.HARD_STOP_STEPS)["verdict"] == "stop"
    assert supervisor.checkpoint(ctx, "x", [h("goto", "succeeded", url=LC)], 5)["verdict"] == "continue"


def test_react_checkpoint_injects_main_agent_message():
    ctx, msgs, hist = _Ctx(), [], []
    for _ in range(5):
        supervisor.note(hist, "fs_read", {"path": "/workspace/a.py"}, {"error": "not found"})
    assert supervisor.react_checkpoint(ctx, "fix tests", hist, 5, msgs) is False
    assert msgs and "[Main Agent, supervising you]" in msgs[-1].content
    assert supervisor.react_checkpoint(ctx, "fix tests", hist, 4, msgs) is False and len(msgs) == 1   # off-cycle


def test_browser_decide_obeys_stop(monkeypatch):
    from aap.graphs import browser as bg
    ctx = _Ctx()
    ctx.node = lambda *a, **k: None
    monkeypatch.setattr(bg, "get_ctx", lambda config: ctx)
    monkeypatch.setattr(supervisor, "checkpoint", lambda *a, **k: {"verdict": "stop", "note": "looping", "signals": ["x"]})
    out = bg.decide({"goal": "g", "steps": 10, "history": []}, {})
    assert out["done"] and out["stalled"] and "Stopped by the Main Agent" in out["answer"] and out["sup_checked"] == 10
