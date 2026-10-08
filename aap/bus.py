"""Process-wide pub/sub used to fan events out to WebSocket clients, channels (Telegram) and the TUI.

Publishers may be in any thread (graphs run in worker threads); subscribers are asyncio queues or plain callbacks.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

log = logging.getLogger("aap.bus")
_lock = threading.Lock()
_queues: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
_callbacks: list[Callable[[str, dict], None]] = []


def publish(channel: str, data: dict) -> None:
    msg = {"channel": channel, "data": data}
    with _lock:
        qs, cbs = list(_queues), list(_callbacks)
    for loop, q in qs:
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except RuntimeError:  # loop closed
            unsubscribe_queue(q)
    for cb in cbs:
        try:
            cb(channel, data)
        except Exception:  # noqa: BLE001
            log.exception("bus callback failed")


def subscribe_queue() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    with _lock:
        _queues.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe_queue(q: asyncio.Queue) -> None:
    with _lock:
        _queues[:] = [(l, x) for l, x in _queues if x is not q]


def on(cb: Callable[[str, dict], None]) -> None:
    with _lock:
        _callbacks.append(cb)
