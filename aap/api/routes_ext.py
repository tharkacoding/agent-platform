"""Browser extras: real Chrome profile import (§11), site memory (§12), Chrome extension pairing + socket (§10)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import clients, extension, settings, site_memory

router = APIRouter()


# ------------------------------------------------------------------------------------------- §11 real profiles
@router.get("/api/browser/real-profiles")
def real_profiles():
    """Your real Chrome profiles (read from Chrome's Local State by the host bridge) + AgentOS's imported copies."""
    try:
        r = clients.bridge("GET", "/chrome/profiles", timeout=30)
    except clients.ServiceUnavailable as e:
        raise HTTPException(503, f"host bridge not running: {e}") from e
    if isinstance(r, dict) and r.get("error") and "profiles" not in r:
        raise HTTPException(502, r["error"])
    return {**r, "active_profile": settings.get("browser_profile") or "default",
            "mode": settings.get("browser_default_mode") or "sandbox"}


class ImportBody(BaseModel):
    profile_dir: str
    name: str = ""


@router.post("/api/browser/import-profile")
def import_profile(body: ImportBody):
    name = re.sub(r"[^A-Za-z0-9_-]", "-", body.name or body.profile_dir).strip("-")[:40] or "imported"
    try:
        r = clients.bridge("POST", "/chrome/import", json={"profile_dir": body.profile_dir, "name": name}, timeout=300)
    except clients.ServiceUnavailable as e:
        raise HTTPException(503, f"host bridge not running: {e}") from e
    if not isinstance(r, dict) or not r.get("ok"):
        raise HTTPException(409, (r or {}).get("error", "import failed") if isinstance(r, dict) else str(r))
    settings.update({"browser_profile": r.get("name", name), "browser_default_mode": "host"})
    return r


# ------------------------------------------------------------------------------------------- §12 site memory
@router.get("/api/site-memory")
def site_memory_list():
    return site_memory.list_sites()


@router.get("/api/site-memory/{domain}")
def site_memory_get(domain: str):
    d = site_memory.load(domain)
    if not d.get("visits"):
        raise HTTPException(404, "no memory for this site")
    return d


@router.delete("/api/site-memory/{domain}")
def site_memory_delete(domain: str):
    return {"ok": site_memory.delete(domain)}


# ------------------------------------------------------------------------------------------- §10 extension
@router.get("/api/extension/status")
def extension_status():
    return extension.status()


@router.get("/api/extension/token")
def extension_token():
    """Plain pairing token (the gateway only listens on 127.0.0.1)."""
    return {"token": extension.token(), "ws_url": "ws://127.0.0.1:7860/ws/extension"}


@router.post("/api/extension/token")
def extension_token_regenerate():
    return {"token": extension.regenerate_token(), "ws_url": "ws://127.0.0.1:7860/ws/extension"}


class UseTab(BaseModel):
    tab_id: int | None = None
    profile: str = ""


@router.post("/api/extension/use-tab")
def extension_use_tab(body: UseTab):
    extension.hub.active_tab = body.tab_id
    extension.hub.active_profile = body.profile if body.tab_id is not None else ""
    if body.tab_id is not None:
        settings.update({"browser_default_mode": "extension"})
        try:
            extension.hub.call("tabs.activate", {"tab_id": body.tab_id}, timeout=10)
        except Exception:  # noqa: BLE001 - selecting still works even if activation fails
            pass
    return extension.status()


class Allowlist(BaseModel):
    allowlist: str = ""


@router.put("/api/extension/allowlist")
async def extension_allowlist(body: Allowlist):
    settings.update({"extension_allowlist": body.allowlist})
    await extension.hub.push_config()
    return extension.status()


class ProfileBody(BaseModel):
    profile: str
    wanted: bool | None = None       # "Allow AgentOS to use this profile" (default off)
    allowlist: str | None = None     # this profile's own allowlist (added to the global one)


@router.put("/api/extension/profile")
async def extension_profile(body: ProfileBody):
    if not body.profile.strip():
        raise HTTPException(400, "profile required")
    extension.set_profile(body.profile, body.wanted, body.allowlist)
    await extension.hub.push_config(body.profile)
    return extension.status()


@router.websocket("/ws/extension")
async def ws_extension(socket: WebSocket):
    if not extension.token_ok(socket.query_params.get("token")):
        await socket.close(code=4401)
        return
    await socket.accept()
    await extension.hub.attach(socket, socket.query_params.get("profile") or "default")
    try:
        while True:
            raw = await socket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                await extension.hub.on_message(msg, socket)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await extension.hub.detach(socket)
