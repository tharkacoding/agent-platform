"""Bridge to the AgentOS Chrome extension (PROJECT_STATE §10).

The extension (extension/ folder, MV3) connects to ws://127.0.0.1:7860/ws/extension?token=<extension_token>.
Gateway → extension:  {"id": "...", "cmd": "<command>", "args": {...}}
Extension → gateway:  {"id": "...", "ok": true, "result": {...}}  |  {"id": "...", "ok": false, "error": "..."}
                      {"type": "hello", "version": "...", "browser": "..."}   {"type": "tabs", "tabs": [...]}
Commands: tabs.list, tabs.open{url}, tabs.activate{tab_id}, tabs.close{tab_id}, screenshot{tab_id},
          snapshot{tab_id}, act{tab_id, action, ref|selector, text, key, dy, submit}, banner{tab_id, on}.
Tools call `hub.call(..., profile=)` from worker threads; it blocks until that profile's extension answers.
One extension per Chrome profile: the socket URL carries ?profile=<name>; the owner switches each profile on
('wanted', default off) and may give it its own allowlist (settings `extension_profiles`).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import uuid
from typing import Any

from . import bus, settings


class ExtensionUnavailable(Exception):
    pass


def norm(profile: str | None) -> str:
    return (profile or "default").strip().lower() or "default"


class Conn:
    """One connected extension = one Chrome profile (each profile has its own install)."""

    def __init__(self, socket, profile: str) -> None:
        self.socket, self.profile = socket, profile
        self.info: dict = {}
        self.email = ""
        self.tabs: list[dict] = []
        self.pending: set[str] = set()


class Hub:
    """Several profiles can be connected at once (key = normalised profile name). The legacy single-extension
    attributes (socket/tabs/info/connected) mirror the 'default' connection = the active one, else the first."""

    def __init__(self) -> None:
        self.conns: dict[str, Conn] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.active_tab: int | None = None           # tab the user picked with "use this tab"
        self.active_profile: str = ""                # profile that tab belongs to ('' = the default connection)
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()

    # ---- lookup ------------------------------------------------------------------------------------------
    def conn(self, profile: str | None = None) -> Conn | None:
        if profile:
            return self.conns.get(norm(profile))
        if self.active_profile and norm(self.active_profile) in self.conns:
            return self.conns[norm(self.active_profile)]
        return next(iter(self.conns.values()), None)

    @property
    def connected(self) -> bool:
        return bool(self.conns)

    @property
    def socket(self):
        c = self.conn()
        return c.socket if c else None

    @property
    def info(self) -> dict:
        c = self.conn()
        return c.info if c else {}

    @property
    def tabs(self) -> list[dict]:
        c = self.conn()
        return c.tabs if c else []

    # ---- connection (runs on the API event loop) -------------------------------------------------------
    async def attach(self, socket, profile: str = "default") -> None:
        key = norm(profile)
        old = self.conns.get(key)
        self.conns[key] = Conn(socket, (profile or "default").strip() or "default")
        self.loop = asyncio.get_running_loop()
        if old is not None:
            try:
                await old.socket.close(code=4000)
            except Exception:  # noqa: BLE001
                pass
        bus.publish("extension", {"connected": True, "profile": key})

    def _of(self, socket) -> Conn | None:
        return next((c for c in self.conns.values() if c.socket is socket), None)

    async def detach(self, socket) -> None:
        c = self._of(socket)
        if c is None:
            return
        self.conns.pop(norm(c.profile), None)
        for rid in list(c.pending):
            f = self._pending.pop(rid, None)
            if f and not f.done():
                f.set_exception(ExtensionUnavailable("extension disconnected"))
        bus.publish("extension", {"connected": bool(self.conns), "profile": norm(c.profile)})

    async def push_config(self, profile: str | None = None) -> None:
        """Send each (or one) connected extension its allowlist (global + its profile's)."""
        for key, c in list(self.conns.items()):
            if profile is None or key == norm(profile):
                try:
                    await c.socket.send_text(json.dumps({"type": "config", "allowlist": allowlist(c.profile),
                                                         "wanted": wanted(c.profile)}))
                except Exception:  # noqa: BLE001
                    pass

    async def on_message(self, msg: dict, socket=None) -> None:
        c = self._of(socket) if socket is not None else self.conn()
        if c is None:
            return
        if msg.get("type") == "hello":
            c.info = {k: msg.get(k) for k in ("version", "browser", "user_agent")}
            c.email = str(msg.get("email") or "")
            await self.push_config(c.profile)
            return
        if msg.get("type") == "tabs":
            c.tabs = msg.get("tabs") or []
            bus.publish("extension", {"connected": True, "tabs": c.tabs, "profile": norm(c.profile)})
            return
        f = self._pending.pop(str(msg.get("id")), None)
        if f and not f.done():
            if msg.get("ok"):
                f.set_result(msg.get("result"))
            else:
                f.set_exception(RuntimeError(msg.get("error") or "extension error"))

    async def _send(self, c: Conn, cmd: str, args: dict, timeout: float) -> Any:
        rid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        c.pending.add(rid)
        await c.socket.send_text(json.dumps({"id": rid, "cmd": cmd, "args": args}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)
            c.pending.discard(rid)

    # ---- called from tool worker threads -------------------------------------------------------------
    def call(self, cmd: str, args: dict | None = None, timeout: float = 45, profile: str | None = None) -> Any:
        """profile=None → the active/first connection (single-extension setups); a name → exactly that profile's
        extension, and only when the owner switched 'Allow AgentOS to use this profile' on."""
        c = self.conn(profile)
        if c is None or self.loop is None:
            raise ExtensionUnavailable(
                f"Chrome extension for profile '{profile}' not connected" if profile else
                "Chrome extension not connected — install it (extension/README.md) and pair it with the token "
                "from Settings → Browser")
        if profile and not wanted(profile):
            raise ExtensionUnavailable(f"profile '{profile}' is not allowed for AgentOS (Browser page → "
                                       "'Allow AgentOS to use this profile')")
        cf = asyncio.run_coroutine_threadsafe(self._send(c, cmd, args or {}, timeout), self.loop)
        return cf.result(timeout + 5)


hub = Hub()


# ---- per-profile settings: {profile: {"wanted": bool, "allowlist": "a.com, b.org"}} (default: not wanted) -------
def profiles_cfg() -> dict:
    raw = settings.get("extension_profiles")
    return {norm(k): dict(v) for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def wanted(profile: str | None) -> bool:
    return bool(profiles_cfg().get(norm(profile), {}).get("wanted"))


def ready(profile: str | None) -> bool:
    """Connected AND the owner allowed AgentOS to use it."""
    return bool(profile) and norm(profile) in hub.conns and wanted(profile)


def set_profile(profile: str, wanted_: bool | None = None, allow: str | None = None) -> dict:
    raw = dict(settings.get("extension_profiles") or {})
    key = next((k for k in raw if norm(k) == norm(profile)), profile.strip())
    cur = dict(raw.get(key) or {})
    if wanted_ is not None:
        cur["wanted"] = bool(wanted_)
    if allow is not None:
        cur["allowlist"] = allow
    raw[key] = cur
    settings.update({"extension_profiles": raw})
    return cur


def token() -> str:
    t = settings.get("extension_token") or ""
    if not t:
        t = regenerate_token()
    return t


def regenerate_token() -> str:
    t = secrets.token_urlsafe(24)
    settings.update({"extension_token": t})
    return t


def token_ok(given: str | None) -> bool:
    import hmac
    return bool(given) and hmac.compare_digest(str(given), token())


def _split(csv: str) -> list[str]:
    return [d.strip().lower() for d in str(csv or "").split(",") if d.strip()]


def allowlist(profile: str | None = None) -> list[str]:
    """Global allowlist + (when given) that profile's own."""
    own = profiles_cfg().get(norm(profile), {}).get("allowlist", "") if profile else ""
    return list(dict.fromkeys(_split(settings.get("extension_allowlist")) + _split(own)))


def site_allowed(url: str, profile: str | None = None) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowlist(profile))


def profiles_status() -> list[dict]:
    cfg = profiles_cfg()
    out = []
    for key in dict.fromkeys(list(hub.conns) + list(cfg)):
        c = hub.conns.get(key)
        out.append({"profile": c.profile if c else key, "key": key, "connected": c is not None,
                    "wanted": bool(cfg.get(key, {}).get("wanted")), "email": c.email if c else "",
                    "allowlist": _split(cfg.get(key, {}).get("allowlist", "")), "tabs": len(c.tabs) if c else 0,
                    "info": c.info if c else {}})
    return out


def status() -> dict:
    return {"connected": hub.connected, "info": hub.info, "tabs": hub.tabs, "active_tab": hub.active_tab,
            "allowlist": allowlist(), "profiles": profiles_status(), "ts": time.time()}
