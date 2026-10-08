"""Capability self-test: runs scripts/run_test_cases.py (docs/TEST_CASES.md §16) against this gateway in a detached
subprocess, exposes progress/results to the API + Tests page, and on the end sends ONE phone summary and appends the
open failures to data/selftest/OPEN_FAILURES.md. Fired on demand (POST /api/selftest/run) or nightly (schedule
"selftest", 03:00). Results are always the harness's independent verification, never the agents' own claims."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("aap.selftest")
DIR = config.DATA_DIR / "selftest"
RESULTS, PROGRESS, LOG, FAILS, STATE = (DIR / n for n in ("test_results.json", "progress.json", "run.log",
                                                         "OPEN_FAILURES.md", "state.json"))
HARNESS = config.ROOT / "scripts" / "run_test_cases.py"
_lock = threading.Lock()


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return not Path(f"/proc/{pid}/status").exists() or "zombie" not in Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False


def home_dir() -> Path:
    """AgentOS host home holding workspace/ + shared/: the data dir in the VM/Docker layout, else ~/AgentOS."""
    if os.environ.get("AAP_HOST_HOME"):
        return Path(os.environ["AAP_HOST_HOME"])
    return config.DATA_DIR if (config.DATA_DIR / "workspace").is_dir() else Path.home() / "AgentOS"


def state() -> dict:
    st = _read(STATE, {})
    return {"running": _alive(int(st.get("pid") or 0)), "sections": st.get("sections", []),
            "started": st.get("started"), "progress": _read(PROGRESS, {})}


def latest() -> dict:
    return _read(RESULTS, {})


def summary(rows: dict | None = None) -> dict:
    rows = latest() if rows is None else rows
    counts = {k: 0 for k in ("pass", "fail", "skipped", "error")}
    for r in rows.values():
        counts[r.get("status", "error")] = counts.get(r.get("status", "error"), 0) + 1
    fails = [{"id": cid, "title": r.get("title", ""), "status": r["status"], "evidence": r.get("evidence", ""),
              "run_id": r.get("run_id", "")} for cid, r in sorted(rows.items()) if r.get("status") in ("fail", "error")]
    return {**counts, "total": len(rows), "failures": fails, "seconds": round(sum(r.get("seconds", 0) for r in rows.values()), 1),
            "tokens": sum(r.get("tokens", 0) for r in rows.values())}


def start(sections: list[str] | None = None) -> dict:
    """Launch the harness detached. Raises RuntimeError if a self-test is already running."""
    sections = [s for s in (sections or ["16"]) if s.strip()] or ["16"]
    with _lock:
        if state()["running"]:
            raise RuntimeError("a self-test is already running")
        DIR.mkdir(parents=True, exist_ok=True)
        PROGRESS.unlink(missing_ok=True)
        home = home_dir()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "AOS_API": os.environ.get("AAP_SELF_URL", "http://127.0.0.1:7860"),
               "AOS_RESULTS": str(RESULTS), "AOS_PROGRESS": str(PROGRESS), "AOS_HOME": str(home),
               "AOS_DATA": str(config.DATA_DIR), "AOS_SETTINGS": str(config.DATA_DIR / "settings.json")}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0   # never flash a console
        with open(LOG, "w", encoding="utf-8") as out:
            p = subprocess.Popen([sys.executable, str(HARNESS), *sections], cwd=str(config.ROOT), env=env,
                                 stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 creationflags=flags, start_new_session=os.name != "nt")
        st = {"pid": p.pid, "sections": sections, "started": time.time()}
        STATE.write_text(json.dumps(st), encoding="utf-8")
    threading.Thread(target=_watch, args=(p, sections), daemon=True, name="selftest-watch").start()
    return st


def _watch(p, sections: list[str]) -> None:
    p.wait()
    try:
        finished(sections)
    except Exception:  # noqa: BLE001
        log.exception("selftest summary failed")


def finished(sections: list[str]) -> str:
    """After a run: one phone push + home feed card + open failures appended to OPEN_FAILURES.md. Returns the text."""
    rows = latest()
    ran = {k: v for k, v in rows.items() if any(k == s or k.startswith(s + ".") for s in sections)}
    s = summary(ran)
    judged = s["pass"] + s["fail"] + s["error"]
    text = f"Capability tests: {s['pass']}/{judged} pass" + (f" ({s['skipped']} skipped)" if s["skipped"] else "")
    if s["failures"]:
        text += " — failed: " + ", ".join(f["id"] for f in s["failures"][:15])
    if s["failures"]:
        lines = [f"\n## {time.strftime('%Y-%m-%d %H:%M')} — sections {', '.join(sections)}"]
        lines += [f"- [ ] {f['id']} {f['title']} ({f['status']}) run={f['run_id'] or '-'} — {f['evidence'][:300]}"
                  for f in s["failures"]]
        with open(FAILS, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    try:
        from . import registry
        registry.add_feed("🧪 Capability tests", text, "", source="selftest")
    except Exception:  # noqa: BLE001
        log.exception("selftest feed card failed")
    from . import notify
    notify.send("AgentOS self-test", text, tags="test_tube", priority="high" if s["failures"] else "")
    return text
