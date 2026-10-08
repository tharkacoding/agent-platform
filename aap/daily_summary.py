"""Daily summary push (TODO §7): one evening message with what AgentOS did today — no LLM call, zero tokens.

Built-in schedule `daily_summary` (21:00, editable on the Schedules page). The phone switch (Settings →
Notifications) still decides whether it is sent.
"""
from __future__ import annotations

import time

from . import registry

_ICON = {"done": "OK", "failed": "FAILED", "cancelled": "stopped", "running": "running"}


def build(now: float | None = None, hours: float = 24) -> tuple[str, str]:
    now = now or time.time()
    since = now - hours * 3600
    runs = [r for r in registry.list_runs(500) if (r.get("created_at") or 0) >= since]
    if not runs:
        return "AgentOS today: nothing ran", "No tasks in the last 24 hours."
    by: dict[str, int] = {}
    for r in runs:
        by[r.get("status") or "?"] = by.get(r.get("status") or "?", 0) + 1
    head = ", ".join(f"{n} {_ICON.get(s, s)}" for s, n in sorted(by.items(), key=lambda x: -x[1]))
    lines = [f"{len(runs)} tasks: {head}", ""]
    failed = [r for r in runs if r.get("status") == "failed"]
    done = [r for r in runs if r.get("status") == "done" and (r.get("kind") or "") != "selftest"]
    for title, rows in (("Failed (look at these):", failed[:5]), ("Done:", done[:6])):
        if rows:
            lines.append(title)
            lines += [f"- {(r.get('request') or '').strip().splitlines()[0][:90]}" for r in rows]
            lines.append("")
    pend = registry.list_approvals("pending")
    if pend:
        lines.append(f"{len(pend)} approval(s) waiting for you.")
    try:
        from . import selftest
        s = selftest.summary()
        if s.get("total"):
            lines.append(f"Self-test: {s.get('pass', 0)}/{s['total']} pass"
                         + (f" - failed: {', '.join(f['id'] for f in s.get('failures', [])[:6])}" if s.get("failures") else ""))
    except Exception:  # noqa: BLE001
        pass
    return f"AgentOS today: {len(runs)} tasks, {len(failed)} failed", "\n".join(lines).strip()


def send() -> bool:
    from . import notify
    title, body = build()
    return notify.send(title, body, tags="calendar", event="finish")
