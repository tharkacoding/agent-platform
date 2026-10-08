"""Ask the USER questions mid-run (clarify what to search, which option, what's next) — shown as a card in chat.

ask() blocks the agent until the user answers (or a timeout: then the agent continues with its stated default and
says so). Answers are plain text; options are suggested quick replies.
"""
from __future__ import annotations

import threading
import time
import uuid

from .. import bus

_pending: dict[str, dict] = {}
_waiters: dict[str, tuple[threading.Event, list]] = {}
_lock = threading.Lock()


def ask(run, agent_id: str, questions: list[dict], timeout: float = 120) -> list[str]:
    """questions: [{"q": str, "options": [str], "default": str}] → answers (default used when unanswered)."""
    qid = uuid.uuid4().hex[:10]
    item = {"id": qid, "run_id": run.run_id, "agent_id": agent_id, "questions": questions, "status": "pending",
            "created_at": time.time(), "timeout_s": timeout}
    ev, box = threading.Event(), []
    with _lock:
        _pending[qid] = item
        _waiters[qid] = (ev, box)
    bus.publish("question", item)
    run.emit("question", agent_id, "waiting", " · ".join(q["q"] for q in questions)[:300], question_id=qid,
             questions=questions, timeout_s=timeout)
    try:
        from .. import notify
        if getattr(run, "notify", False):
            notify.waiting(run.run_id, "Question: " + " / ".join(q["q"] for q in questions)[:400])
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + timeout
    while not ev.wait(0.5):
        if run.cancelled or time.time() > deadline:
            break
    with _lock:
        _waiters.pop(qid, None)
        item = _pending.pop(qid, item)
    answered = bool(box)
    answers = box[0] if answered else [q.get("default", "") for q in questions]
    item.update(status="answered" if answered else "defaulted", answers=answers)
    bus.publish("question", item)
    run.emit("question", agent_id, "done", ("answered: " if answered else "no answer — using defaults: ")
             + " | ".join(a for a in answers if a)[:300], question_id=qid, answers=answers, defaulted=not answered)
    return answers


def answer(qid: str, answers: list[str]) -> bool:
    with _lock:
        w = _waiters.get(qid)
    if not w:
        return False
    w[1].append([str(a) for a in answers])
    w[0].set()
    return True


def pending(run_id: str = "") -> list[dict]:
    with _lock:
        return [q for q in _pending.values() if not run_id or q["run_id"] == run_id]
