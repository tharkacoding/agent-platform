"""Scheduled tasks: standard 5-field cron (min hour dom month dow), checked every 20s in a daemon thread.
Each due job starts a Main Agent run; the answer becomes a Home feed card (and is pushed to channels)."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import uuid

from .. import registry

log = logging.getLogger("aap.scheduler")
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
ALIASES = {"@hourly": "0 * * * *", "@daily": "0 8 * * *", "@weekly": "0 8 * * 1", "@monthly": "0 8 1 * *"}


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron value out of range: {part}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> list[set[int]]:
    expr = ALIASES.get(expr.strip(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields: min hour day month weekday")
    sets = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES)]
    sets[4] = {d % 7 for d in sets[4]}
    return sets


def matches(sets: list[set[int]], t: dt.datetime) -> bool:
    dow = (t.weekday() + 1) % 7   # cron: 0=Sunday
    return (t.minute in sets[0] and t.hour in sets[1] and t.day in sets[2] and t.month in sets[3] and dow in sets[4])


def next_run(expr: str, after: dt.datetime | None = None) -> float | None:
    sets = parse(expr)
    t = (after or dt.datetime.now()).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(60 * 24 * 370):
        if matches(sets, t):
            return t.timestamp()
        t += dt.timedelta(minutes=1)
    return None


def create(name: str, cron: str, prompt: str, enabled: bool = True, channel: str = "") -> dict:
    parse(cron)
    s = {"id": uuid.uuid4().hex[:10], "name": name, "cron": cron, "prompt": prompt, "enabled": enabled,
         "channel": channel, "last_run": None, "next_run": next_run(cron), "last_run_id": ""}
    return registry.save_schedule(s)


def run_now(s: dict) -> str:
    if s.get("kind") == "selftest":   # capability suite: a harness subprocess (results on the Tests page), never a chat
        from .. import selftest
        try:
            st = selftest.start(s.get("sections") or ["16"])
            rid = f"selftest-{int(st['started'])}"
        except RuntimeError:
            rid = "selftest-already-running"
        s.update(last_run=time.time(), last_run_id=rid, next_run=next_run(s["cron"]))
        registry.save_schedule(s)
        return rid
    if s.get("kind") == "daily_summary":   # evening phone summary of today's tasks: no agents, no tokens
        from .. import daily_summary
        sent = daily_summary.send()
        s.update(last_run=time.time(), last_run_id="summary-sent" if sent else "summary-off", next_run=next_run(s["cron"]))
        registry.save_schedule(s)
        return s["last_run_id"]
    from ..core.runtime import runtime

    def done(run, rep, final):
        registry.add_feed(f"⏰ {s['name']}", final, run.run_id, source=f"schedule:{s['id']}")
        if s.get("channel"):
            from ..channels import base
            base.notify(f"⏰ {s['name']}\n\n{final}")

    prompt = s["prompt"]
    if s.get("kind") == "heartbeat":   # OpenClaw-style heartbeat: run the editable HEARTBEAT.md checklist
        from .. import souls
        prompt = ("Heartbeat check. Go through this checklist and report ONLY what needs my attention "
                  "(say 'All clear' if nothing does):\n\n" + souls.get_file("HEARTBEAT"))
    run_id = runtime.start_run(prompt, kind="schedule", on_done=done)   # a run, not a chat
    s.update(last_run=time.time(), last_run_id=run_id, next_run=next_run(s["cron"]))
    registry.save_schedule(s)
    return run_id


def ensure_heartbeat() -> None:
    """Built-in 'heartbeat' schedule (disabled by default; enable it on the Schedules page)."""
    if not any(x.get("id") == "heartbeat" for x in registry.list_schedules()):
        registry.save_schedule({"id": "heartbeat", "name": "Heartbeat", "cron": "0 9,18 * * *",
                                "prompt": "(runs HEARTBEAT.md)", "enabled": False, "channel": "default",
                                "kind": "heartbeat", "last_run": None, "next_run": next_run("0 9,18 * * *"),
                                "last_run_id": ""})


def ensure_selftest() -> None:
    """Built-in nightly capability self-test (enabled by default, 03:00 when the owner is idle; editable)."""
    if not any(x.get("id") == "selftest" for x in registry.list_schedules()):
        registry.save_schedule({"id": "selftest", "name": "Capability self-test", "cron": "0 3 * * *",
                                "prompt": "(runs the docs/TEST_CASES.md §16 capability suite)", "enabled": True,
                                "channel": "", "kind": "selftest", "sections": ["16"], "last_run": None,
                                "next_run": next_run("0 3 * * *"), "last_run_id": ""})


def ensure_daily_summary() -> None:
    """Built-in 21:00 phone summary (what ran today, what failed, approvals waiting); the phone switch still applies."""
    if not any(x.get("id") == "daily_summary" for x in registry.list_schedules()):
        registry.save_schedule({"id": "daily_summary", "name": "Daily summary to phone", "cron": "0 21 * * *",
                                "prompt": "(sends today's task summary to the phone)", "enabled": True,
                                "channel": "", "kind": "daily_summary", "last_run": None,
                                "next_run": next_run("0 21 * * *"), "last_run_id": ""})


class Scheduler(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="scheduler")
        self._last_minute = None

    def run(self) -> None:
        while True:
            try:
                now = dt.datetime.now().replace(second=0, microsecond=0)
                if now != self._last_minute:
                    self._last_minute = now
                    for s in registry.list_schedules():
                        if s.get("enabled") and matches(parse(s["cron"]), now):
                            log.info("schedule %s due", s["name"])
                            run_now(s)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            time.sleep(20)


_started = False


def start() -> None:
    global _started
    if not _started:
        try:
            ensure_heartbeat()
            ensure_selftest()
            ensure_daily_summary()
        except Exception:  # noqa: BLE001
            log.exception("built-in schedule setup failed")
        Scheduler().start()
        _started = True
