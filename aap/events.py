"""Run context + event stream. Graph nodes run in worker threads and call ctx.emit(); events are persisted,
kept in memory for SSE, and fanned out over the bus to WebSocket clients."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import bus, registry


@dataclass
class RunContext:
    run_id: str
    kind: str = "chat"
    conversation_id: str = ""
    events: list[dict] = field(default_factory=list)
    done: bool = False
    cancelled: bool = False
    steer: list = field(default_factory=list)     # messages the user sent WHILE this run works (change of track)
    notify: bool = False                          # phone push for this run: only chats the OWNER started
    directives: str = ""                          # slash-mode directives for this request (aap/modes.py)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_steer(self, text: str) -> dict:
        item = {"text": text.strip()[:4000], "ts": time.time(), "n": len(self.steer) + 1}
        with self._lock:
            self.steer.append(item)
        self.emit("steer", "user", "info", item["text"], n=item["n"])
        return item

    def steer_block(self, since: int = 0) -> str:
        msgs = self.steer[since:]
        if not msgs:
            return ""
        return ("USER UPDATES sent while you were working (newest last). They OVERRIDE the original instructions "
                "where they conflict — follow them now:\n" + "\n".join(f"- {m['text']}" for m in msgs))

    def emit(self, type: str, agent_id: str = "", status: str = "", message: str = "", **data: Any) -> dict:
        """type: run|node|agent|tool|plan|build|approval|log|report|final|token"""
        ev = {"ts": time.time(), "run_id": self.run_id, "type": type, "agent_id": agent_id,
              "status": status, "message": message, "data": data}
        with self._lock:
            ev["seq"] = len(self.events)
            self.events.append(ev)
        if type != "token":
            registry.add_event(self.run_id, ev)
        bus.publish("run", ev)
        return ev

    def check_cancel(self) -> None:
        if self.cancelled:
            raise RunCancelled("run cancelled by user")


class RunCancelled(Exception):
    pass


_runs: dict[str, RunContext] = {}


def create(run_id: str, kind: str = "chat", conversation_id: str = "") -> RunContext:
    ctx = RunContext(run_id, kind, conversation_id)
    _runs[run_id] = ctx
    return ctx


def get(run_id: str) -> RunContext | None:
    return _runs.get(run_id)


def active() -> list[RunContext]:
    return [r for r in _runs.values() if not r.done]
