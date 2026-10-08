"""Phone → AgentOS / Claude Code through ntfy (https://ntfy.sh).

The owner types in the ntfy app on topic <notify_ntfy_url>:
  * `claude …`  → for Claude Code (the AI developing this repo): appended to data/claude_inbox.jsonl, which the
                  Claude Code session watches; it replies by push.
  * `yes`/`no`  → decides the pending approval of the phone task
  * any text while the phone task waits on a question card → the answer
  * any text while the phone task is still working → a mid-run message (changes its course)
  * anything else → a new AgentOS task in the "📱 Phone" chat; finished/failed/waiting pushes come back.
Our own outbound pushes carry the tag `aos-out` and are ignored here (no feedback loops).
The owner chose an unprotected topic; dangerous actions still always ask for approval (aap/core/approvals.py).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

from .. import config, events, registry, settings

log = logging.getLogger("aap.ntfy")
OUT_TAG = "aos-out"
CLAUDE_RE = re.compile(r"^\s*claude\b[\s,:;.!-]*", re.I)
YES_RE = re.compile(r"^\s*(yes|y|ok|okay|approve|approved|allow|go|do it)\s*[.!]*\s*$", re.I)
SHOT_RE = re.compile(r"^\s*(send (me )?(a |the )?)?(screen ?shot|ss|screen|show (me )?(the )?screen)s?\s*[.!?]*\s*$", re.I)
NO_RE = re.compile(r"^\s*(no|n|deny|denied|reject|stop|cancel|don'?t)\s*[.!]*\s*$", re.I)


def _state_file():
    return config.DATA_DIR / "ntfy_state.json"


def inbox_file():
    return config.DATA_DIR / "claude_inbox.jsonl"


def _load() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(st: dict) -> None:
    try:
        _state_file().write_text(json.dumps(st), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def topic_url() -> str:
    return (settings.get("notify_ntfy_url") or "").strip().rstrip("/")


def _push(title: str, body: str) -> None:
    from .. import notify
    notify.send(title, body, event="finish", sync=False, force=True)


def route(text: str, st: dict) -> dict:
    """Decide what one inbound phone message does. Returns {"action": ..., ...} and updates `st` in place.
    Pure enough to unit-test: side effects go through the runtime/approvals/questions modules."""
    text = (text or "").strip()
    if not text:
        return {"action": "ignored"}
    if CLAUDE_RE.match(text):
        msg = CLAUDE_RE.sub("", text, count=1).strip() or text
        item = {"id": f"in{int(time.time() * 1000)}", "text": msg, "ts": time.time(), "source": "ntfy"}
        with open(inbox_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        _push("Sent to Claude", f"📨 Got it — Claude will read: {msg[:200]}")
        return {"action": "claude", "item": item}
    rid = st.get("phone_run", "")
    live = events.get(rid) if rid else None
    working = bool(live and not live.done)
    if SHOT_RE.match(text):   # "screenshot" → a picture of the screen right now (MISTAKES.txt M9)
        from .. import notify
        png = notify.screen_png()
        note = __import__("aap.core.runstatus", fromlist=["describe"]).describe(live) if working else "No task running."
        if png:
            notify.send_image("Your screen now", note, png, force=True, filename="screen.jpg")
        else:
            _push("AgentOS", "Couldn't take a screenshot (the desktop bridge isn't running).")
        return {"action": "screenshot", "ok": bool(png)}
    if working:
        from ..core import approvals, questions
        if YES_RE.match(text) or NO_RE.match(text):
            mine = [a for a in approvals.pending() if a.get("run_id") == rid]
            if mine:
                ok = bool(YES_RE.match(text))
                approvals.decide(mine[-1]["id"], "approve" if ok else "deny")
                _push("AgentOS", ("Approved: " if ok else "Denied: ") + str(mine[-1].get("title", ""))[:200])
                return {"action": "approval", "approved": ok}
        qs = questions.pending(rid)
        if qs:
            q = qs[-1]
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            n = len(q.get("questions") or [])
            ans = [(lines[i] if i < len(lines) else (q["questions"][i].get("default") or "")) for i in range(n)] \
                if n > 1 else [text]
            questions.answer(q["id"], ans)
            return {"action": "answer", "answers": ans}
        from ..core import runstatus
        if runstatus.STOP_RE.match(text):
            live.cancelled = True
            _push("AgentOS", "⏹ Stopped the task.")
            return {"action": "stop"}
        if runstatus.is_status_question(text):   # a QUESTION about the task: answer it, don't change the plan
            from .. import notify
            png = notify.screen_png()
            if png:   # status + what the screen looks like right now
                notify.send_image("AgentOS status", runstatus.describe(live), png, force=True, filename="status.jpg")
            else:
                _push("AgentOS status", runstatus.describe(live))
            return {"action": "status"}
        live.add_steer(text)
        _push("AgentOS", f"↪ Changing course: {text[:200]}")
        return {"action": "steer"}
    from ..core.runtime import runtime
    st["phone_cid"] = registry.ensure_conversation(st.get("phone_cid") or None, "📱 Phone")
    run_id, cid = runtime.start_chat(text, st["phone_cid"], notify=True)
    st["phone_cid"], st["phone_run"] = cid, run_id
    return {"action": "task", "run_id": run_id}


class Ntfy:
    """Listens on the ntfy topic (streaming JSON) in a daemon thread."""
    name = "ntfy"

    def __init__(self):
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="ntfy-inbound", daemon=True).start()

    def send_owner(self, text: str) -> None:   # channels.base.notify → phone
        _push("AgentOS", text)

    def _loop(self) -> None:
        import httpx
        backoff = 2.0
        while not self._stop.is_set():
            url = topic_url()
            if not url or not settings.get("ntfy_inbound"):
                time.sleep(10)
                continue
            st = _load()
            since = st.get("last_id") or str(int(time.time()) - 60)   # first start: only the last minute
            try:
                with httpx.stream("GET", url + "/json", params={"since": since},
                                  timeout=httpx.Timeout(10, read=120)) as r:
                    if r.status_code >= 400:
                        r.read()
                        if "since" in (r.text or "").lower() and st.get("last_id"):
                            st.pop("last_id", None)   # message id expired on the server: restart from now
                            _save(st)
                        raise RuntimeError(f"ntfy HTTP {r.status_code}: {r.text[:120]}")
                    backoff = 2.0
                    for line in r.iter_lines():
                        if self._stop.is_set() or not settings.get("ntfy_inbound") or topic_url() != url:
                            break
                        self.handle_line(line)
            except Exception as e:  # noqa: BLE001
                log.warning("ntfy stream: %s (retry in %.0fs)", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    @staticmethod
    def handle_line(line: str) -> dict | None:
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            return None
        if msg.get("event") != "message":
            return None
        st = _load()
        st["last_id"] = msg.get("id") or st.get("last_id")
        out = None
        if OUT_TAG not in (msg.get("tags") or []):          # never react to our own pushes
            try:
                out = route(msg.get("message", ""), st)
                log.info("phone message routed: %s", out.get("action"))
            except Exception:  # noqa: BLE001
                log.exception("phone message failed")
        _save(st)
        return out
