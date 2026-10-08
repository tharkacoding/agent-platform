"""The test VM's browser (M7: work WITHOUT the owner's screen/mouse).

The Linux VM (WSL2 "agentos-vm", scripts/vm_up.sh) runs its own Chromium on a hidden display (:99) behind a normal
browser_svc. The owner logs in to a site ONCE in a VM profile (Browser page → "Log in on the VM screen", watched via
noVNC); from then on web tasks for that profile run there through Playwright — no mouse, no keyboard, no window on
the owner's desktop. Settings: vm_browser_url (e.g. http://127.0.0.1:8012), vm_view_url (noVNC page).
"""
from __future__ import annotations

import time
from typing import Any

from . import settings
from .clients import ServiceUnavailable, _call

_CACHE: dict[str, Any] = {"ts": 0.0, "profiles": set(), "ok": False}


def url() -> str:
    return (settings.get("vm_browser_url") or "").rstrip("/")


def view_url() -> str:
    return settings.get("vm_view_url") or "http://localhost:6081/vnc.html?autoconnect=1&resize=scale"


def call(method: str, path: str, **kw) -> Any:
    if not url():
        raise ServiceUnavailable("VM browser not configured (Settings → Browser → VM browser URL)")
    return _call(url(), method, path, **kw)


def profiles(refresh: bool = False) -> set[str]:
    """Profiles that exist in the VM browser (lower-case); AgentOS's own 'default' never counts as a login."""
    if not url():
        return set()
    if refresh or time.time() - _CACHE["ts"] > 60:
        try:
            r = call("GET", "/profiles", timeout=3)
            _CACHE.update(profiles={p.lower() for p in (r.get("profiles") or []) if p.lower() != "default"}, ok=True)
        except Exception:  # noqa: BLE001 - VM off → simply not used
            _CACHE.update(profiles=set(), ok=False)
        _CACHE["ts"] = time.time()
    return _CACHE["profiles"]


def has(profile: str) -> bool:
    return bool(profile) and profile.strip().lower() in profiles()


def status() -> dict:
    p = profiles(refresh=True)
    return {"configured": bool(url()), "available": _CACHE["ok"], "url": url(), "view_url": view_url(),
            "profiles": sorted(p)}
