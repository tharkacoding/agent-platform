"""Human-in-the-loop approvals shared by the web UI, TUI and Telegram.

A graph node (worker thread) calls request(); it blocks until someone decides via decide() or it times out (deny).
'Allow for N minutes' grants auto-approve identical (agent, tool) requests for that window.
"""
from __future__ import annotations

import re
import threading
import time
import uuid

from .. import bus, config, registry

_waiters: dict[str, tuple[threading.Event, list]] = {}
_grants: dict[tuple[str, str], float] = {}     # (agent_id, title-prefix) -> expires
_run_grants: set[tuple[str, str, str]] = set()  # (run_id, agent_id, title) approved once → not asked again this run
_lock = threading.Lock()
_PER_ACTION: tuple = ()                         # desktop control: one approval per task (see tools/desktop.py)


# Even in auto mode these always ask: destroying data, money, credentials, publishing/sending as the user
_ALWAYS_ASK = re.compile(r"(?i)destructive|rm -rf|git push|purchase|\bbuy\b|\bpay(ment)?\b|checkout|transfer|"
                         r"password|delete (my|the|your) account|wire money|place order|DELETE files")


def _grant_key(agent_id: str, title: str) -> tuple[str, str]:
    return agent_id, title.split(":")[0]


def request(run, agent_id: str, title: str, detail: dict, timeout: float | None = None) -> bool:
    key = _grant_key(agent_id, title)
    if _grants.get(key, 0) > time.time():
        run.emit("approval", agent_id, "done", f"auto-approved (standing grant): {title}")
        return True
    rkey = (run.run_id, agent_id, title)
    if rkey in _run_grants and not title.startswith(_PER_ACTION):
        run.emit("approval", agent_id, "done", f"already approved in this run: {title}")
        return True
    aid = uuid.uuid4().hex[:10]
    from .. import settings
    if settings.get("auto_mode") and not _ALWAYS_ASK.search(title):
        # AUTO MODE: approved without a click (recorded + audited; the bridge still refuses screen control while
        # the user is using the PC). Truly dangerous actions (see _ALWAYS_ASK) still ask.
        registry.add_approval({"id": aid, "run_id": run.run_id, "agent_id": agent_id, "title": title,
                               "detail": detail, "status": "approved", "created_at": time.time(), "decided_at": None})
        registry.set_approval(aid, "approved")
        with _lock:
            _run_grants.add(rkey)
        run.emit("approval", agent_id, "done", f"auto-approved (auto mode): {title}", approval_id=aid, auto=True)
        return True
    a = {"id": aid, "run_id": run.run_id, "agent_id": agent_id, "title": title, "detail": detail,
         "status": "pending", "created_at": time.time(), "decided_at": None}
    ev, box = threading.Event(), []
    with _lock:
        _waiters[aid] = (ev, box)
    registry.add_approval(a)
    bus.publish("approval", a)
    run.emit("approval", agent_id, "waiting", title, approval_id=aid, detail=detail)
    try:
        from .. import notify
        if getattr(run, "notify", False):
            notify.waiting(run.run_id, "Approval needed: " + title)
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + (timeout or config.APPROVAL_TIMEOUT)
    while not ev.wait(0.5):
        if run.cancelled or time.time() > deadline:
            break
    with _lock:
        _waiters.pop(aid, None)
    ok = bool(box and box[0] == "approve")
    if ok:
        with _lock:
            if len(_run_grants) > 5000:
                _run_grants.clear()
            _run_grants.add(rkey)
    if not box:
        registry.set_approval(aid, "expired")
    run.emit("approval", agent_id, "done" if ok else "failed", ("approved: " if ok else "denied: ") + title,
             approval_id=aid)
    return ok


def decide(aid: str, decision: str, remember_minutes: float = 0) -> bool:
    status = "approved" if decision == "approve" else "denied"
    registry.set_approval(aid, status)
    with _lock:
        w = _waiters.get(aid)
    rows = [a for a in registry.list_approvals() if a["id"] == aid]
    if rows:
        a = dict(rows[0], status=status)
        bus.publish("approval", a)
        if remember_minutes and decision == "approve":
            _grants[_grant_key(a["agent_id"], a["title"])] = time.time() + remember_minutes * 60
    if w:
        w[1].append(decision)
        w[0].set()
        return True
    return False


def policy(agent_id: str, tool: str, args: dict) -> str | None:
    """User autonomy rules → 'allow' | 'ask' | 'never' | None (no rule matched). Precedence never > ask > allow."""
    from fnmatch import fnmatch
    from .. import settings
    subject = f"{agent_id}:{tool}:" + " ".join(str(v) for v in args.values())[:300]
    hits = {r.get("policy") for r in settings.get("autonomy_rules") or []
            if isinstance(r, dict) and r.get("pattern") and fnmatch(subject, r["pattern"])}
    for p in ("never", "ask", "allow"):
        if p in hits:
            return p
    return None


def pending() -> list[dict]:
    return registry.list_approvals("pending")
