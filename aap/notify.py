"""Phone push notifications via ntfy (https://ntfy.sh) — sent whenever a task finishes, fails, is stopped, or is
waiting for you (a question card or an approval).

Settings (Settings page → Notifications):
  notify_ntfy_url   full topic URL, e.g. https://ntfy.sh/omreddyenduri ('' = off)
  notify_on         all (finished + failed + stopped + waiting for you) | finish (only when a run ends) | off
  notify_priority   min | low | default | high | urgent
Sending is fire-and-forget on a background thread: a slow/unreachable ntfy server never delays the agents.
"""
from __future__ import annotations

import logging
import threading

from . import settings

log = logging.getLogger("aap.notify")


def _cfg() -> tuple[str, str, str]:
    return ((settings.get("notify_ntfy_url") or "").strip(), (settings.get("notify_on") or "all").lower(),
            (settings.get("notify_priority") or "high").lower())


def _ascii(s: str) -> str:   # HTTP header values must be latin-1: keep titles plain
    return s.encode("ascii", "ignore").decode().strip()[:120] or "AgentOS"


def send(title: str, body: str, priority: str = "", tags: str = "", event: str = "finish",
         sync: bool = False, force: bool = False) -> bool:
    """event: finish (run ended) | waiting (needs the user). Returns False when notifications are off.
    force=True: a direct reply to something the owner sent from the phone (ignores the on/off switch)."""
    url, on, prio = _cfg()
    if settings.get("llm_provider") == "mock" and not sync:   # offline/test runs never push to the phone
        return False
    if not settings.get("notify_enabled") and not sync and not force:   # master switch (test button always works)
        return False
    if not url or (not force and (on == "off" or (on == "finish" and event != "finish"))):
        return False

    def go():
        try:
            import httpx
            headers = {"Title": _ascii(title), "Priority": priority or prio}
            # every outbound push is tagged aos-out so the phone listener (channels/ntfy.py) never reads it back
            headers["Tags"] = _ascii(",".join(t for t in (tags, "aos-out") if t))
            httpx.post(url, content=(body or title)[:3500].encode("utf-8"), headers=headers, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy notification failed: %s", e)

    if sync:
        go()
    else:
        threading.Thread(target=go, daemon=True).start()
    return True


def send_image(title: str, body: str, png: bytes, force: bool = False, filename: str = "screen.png") -> bool:
    """Push a screenshot to the phone (ntfy attachment; the text goes in the Message header)."""
    url, on, prio = _cfg()
    if not png or not url or settings.get("llm_provider") == "mock":
        return False
    if not force and (not settings.get("notify_enabled") or on == "off"):
        return False

    def go():
        try:
            import httpx
            # header values must be plain ASCII (latin-1): newlines become " | "
            msg = " | ".join(x.strip() for x in body.encode("ascii", "ignore").decode().splitlines() if x.strip())
            httpx.put(url, content=png, timeout=30,
                      headers={"Title": _ascii(title), "Filename": filename, "Tags": "camera,aos-out",
                               "Message": msg[:900] or _ascii(title), "Priority": prio})
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy image push failed: %s", e)

    threading.Thread(target=go, daemon=True).start()
    return True


def screen_png() -> bytes:
    """A screenshot of the owner's screen right now (via the host bridge), shrunk for the phone. b'' if unavailable."""
    try:
        from .clients import bridge
        png = bridge("GET", "/desktop/screenshot")
        if not isinstance(png, (bytes, bytearray)):
            return b""
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(png))
            if im.width > 1280:
                im = im.resize((1280, int(im.height * 1280 / im.width)))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=80)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return bytes(png)
    except Exception:  # noqa: BLE001
        return b""


def last_run_screenshot(run) -> bytes:
    """The most recent screenshot a run saved (/shared/screenshots/...), read from the host's shared folder."""
    from pathlib import Path
    import re as _re
    for e in reversed(list(getattr(run, "events", []) or [])):
        if e.get("type") != "tool":
            continue
        m = _re.search(r"/shared/screenshots/[\w.-]+\.png", str((e.get("data") or {}).get("output") or ""))
        if m:
            p = Path.home() / "AgentOS" / m.group(0).lstrip("/")
            if p.exists():
                return p.read_bytes()
    return b""


def run_finished(run_id: str, kind: str, request: str, status: str, final: str, run=None) -> bool:
    icon = {"done": "white_check_mark", "failed": "x", "cancelled": "stop_sign"}.get(status, "bell")
    word = {"done": "Done", "failed": "Failed", "cancelled": "Stopped"}.get(status, status.title())
    title = f"AgentOS: {word} - {request.strip().splitlines()[0][:60] if request else kind}"
    ok = send(title, (final or word)[:1200], tags=icon, event="finish", priority="high" if status != "done" else "")
    if ok and run is not None:   # show the end state too: the task's last screenshot, if it took any
        png = last_run_screenshot(run)
        if png:
            send_image(f"{word}: last screenshot", "", png)
    return ok


def waiting(run_id: str, what: str) -> bool:
    return send("AgentOS needs you", what[:500], tags="raising_hand", event="waiting")
