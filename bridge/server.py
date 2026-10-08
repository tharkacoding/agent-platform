"""Host Bridge HTTP server (127.0.0.1:8765). docs/CONTRACTS.md §5.

Every request needs X-Bridge-Token. Desktop and Chrome features are disabled unless started with --desktop/--chrome.
Every /desktop/act needs an X-Approval-Id the gateway reports as approved, or a local Yes in a Windows dialog
(auto-deny after 60 s).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import chrome, security

log = logging.getLogger("bridge")


class ChromeLaunch(BaseModel):
    profile_dir: Optional[str] = None
    profile_name: Optional[str] = None
    # who will attach over CDP: "docker" (containers → forwarder on host.docker.internal:9223) or "local"
    # (a process on this machine, e.g. dev-mode browser_svc → Chrome's own 127.0.0.1:9222). Default: env
    # BRIDGE_CDP_PUBLIC, else "docker".
    cdp_public: Optional[str] = None


class DesktopAct(BaseModel):
    action: str
    ref: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    keys: Optional[list[str] | str] = None
    app: Optional[str] = None


class OpenReal(BaseModel):
    profile: str
    url: Optional[str] = None


def create_app(token: str, desktop: bool = False, chrome_enabled: bool = False,
               gateway_url: str = "http://127.0.0.1:7860", confirm=security.local_confirm,
               approved=security.gateway_approved, start_forwarder: bool = True) -> FastAPI:
    app = FastAPI(title="AgentOS Host Bridge", version="0.1.0")
    state = {"forwarder": None}

    def auth(x_bridge_token: Optional[str] = Header(default=None)) -> None:
        if not security.token_ok(token, x_bridge_token):
            raise HTTPException(401, "missing or bad X-Bridge-Token")

    def need(flag: bool, name: str) -> None:
        if not flag:
            raise HTTPException(403, f"{name} is disabled — start the bridge with --{name}")

    @app.get("/health", dependencies=[Depends(auth)])
    def health() -> dict:
        return {"ok": True, "features": {"desktop": desktop, "chrome": chrome_enabled}}

    @app.post("/chrome/launch", dependencies=[Depends(auth)])
    def chrome_launch(body: ChromeLaunch) -> dict:
        need(chrome_enabled, "chrome")
        try:
            udd = security.resolve_profile_dir(body.profile_dir, body.profile_name)
        except security.ProfileRefused as e:
            raise HTTPException(400, str(e)) from e
        try:
            info = chrome.launch(udd, body.profile_name)
        except (FileNotFoundError, TimeoutError, RuntimeError) as e:
            raise HTTPException(500, str(e)) from e
        public = (body.cdp_public or os.environ.get("BRIDGE_CDP_PUBLIC") or "docker").lower()
        if public not in ("docker", "local"):
            raise HTTPException(400, "cdp_public must be 'docker' or 'local'")
        if public == "local":
            return {"cdp_url": f"http://127.0.0.1:{chrome.CDP_PORT}", "user_data_dir": str(udd), **info}
        if start_forwarder and state["forwarder"] is None:
            from .forwarder import CdpForwarder
            state["forwarder"] = CdpForwarder(chrome.PUBLIC_PORT, chrome.CDP_PORT).start()
        return {"cdp_url": f"http://host.docker.internal:{chrome.PUBLIC_PORT}", "user_data_dir": str(udd), **info}

    @app.post("/chrome/open_real", dependencies=[Depends(auth)])
    def chrome_open_real(body: OpenReal) -> dict:
        """Open a URL in the owner's REAL Chrome profile (normal Chrome, no debug port — Chrome forbids automation
        of real profiles). Agents then drive it with desktop actions. Profile = display name ('asdf') or dir."""
        need(chrome_enabled, "chrome")
        from . import profiles
        lst = profiles.list_profiles().get("profiles", [])
        want = (body.profile or "").strip().lower()
        match = next((p for p in lst if want in (p["dir"].lower(), (p.get("name") or "").lower())), None)
        if not match:
            raise HTTPException(404, f"no real Chrome profile named '{body.profile}'. "
                                     f"Available: {', '.join(p.get('name') or p['dir'] for p in lst)}")
        exe = chrome.find_chrome()
        if not exe:
            raise HTTPException(500, "Chrome not found")
        import subprocess
        subprocess.Popen([exe, f"--profile-directory={match['dir']}", body.url or "about:blank"], close_fds=True)
        return {"ok": True, "profile": match.get("name") or match["dir"], "dir": match["dir"],
                "window_suffix": f"- {match.get('name') or match['dir']}"}

    @app.get("/chrome/active", dependencies=[Depends(auth)])
    def chrome_active() -> dict:
        """Which profile the AgentOS debug Chrome is REALLY running (used to verify before acting)."""
        return chrome.active()

    @app.get("/chrome/profiles", dependencies=[Depends(auth)])
    def chrome_profiles() -> dict:
        """The owner's REAL Chrome profiles (read-only listing) + AgentOS's own imported profiles."""
        from . import profiles
        out = profiles.list_profiles()
        base = security.PROFILES_DIR
        out["agentos_profiles"] = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []
        return out

    class ImportBody(BaseModel):
        profile_dir: str
        name: str = ""

    @app.post("/chrome/import", dependencies=[Depends(auth)])
    def chrome_import(body: ImportBody) -> dict:
        need(chrome_enabled, "chrome")
        from . import profiles
        if chrome.cdp_alive():
            raise HTTPException(409, "the AgentOS Chrome window is open — close it (or run scripts\\dev_down.ps1) "
                                     "before importing, so the copied profile can be written")
        try:
            return profiles.import_profile(body.profile_dir, body.name or body.profile_dir)
        except (profiles.ImportRefused, security.ProfileRefused) as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/desktop/idle", dependencies=[Depends(auth)])
    def desktop_idle() -> dict:
        """Is the owner using the computer? Agents only take over the screen when they are not."""
        from . import desktop as d
        return {"idle_seconds": round(d.user_idle_seconds(), 1), "owner_active": d.owner_active()}

    @app.get("/desktop/screenshot", dependencies=[Depends(auth)])
    def desktop_screenshot() -> Response:
        need(desktop, "desktop")
        from . import desktop as d
        return Response(d.screenshot_png(), media_type="image/png")

    @app.get("/desktop/tree", dependencies=[Depends(auth)])
    def desktop_tree(window: str = "", contains: str = "") -> dict:
        need(desktop, "desktop")
        from . import desktop as d
        return d.tree(window, contains)

    @app.post("/desktop/act", dependencies=[Depends(auth)])
    async def desktop_act(body: DesktopAct, x_approval_id: Optional[str] = Header(default=None)) -> dict:
        need(desktop, "desktop")
        ok = bool(x_approval_id) and await asyncio.to_thread(approved, gateway_url, x_approval_id)
        if not ok:
            summary = describe(body)
            ok = await asyncio.to_thread(confirm, "AgentOS — allow desktop action?",
                                         f"An agent wants to: {summary}\n\nAllow? (auto-deny in 60 s)")
        if not ok:
            raise HTTPException(403, "desktop action denied")
        from . import desktop as d
        try:
            return await asyncio.to_thread(d.act, **body.model_dump())
        except (KeyError, ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e

    app.state.bridge = state  # forwarder threads are daemons; they die with the process
    return app


def describe(a: DesktopAct) -> str:
    target = a.ref or (f"({a.x},{a.y})" if a.x is not None else "")
    extra = a.text or (a.keys if isinstance(a.keys, str) else "+".join(a.keys or [])) or a.app or ""
    return f"{a.action} {target} {extra[:120]}".strip()


def serve(desktop: bool = False, chrome: bool = False, port: int = 8765,
          gateway_url: str = "http://127.0.0.1:7860") -> None:
    import uvicorn
    from rich.console import Console

    from . import killswitch

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    console = Console()
    token = security.load_or_create_token()
    killed = killswitch.install()
    console.rule("[bold]AgentOS Host Bridge")
    console.print(f"listening on http://127.0.0.1:{port}  (containers: http://host.docker.internal:{port})")
    console.print(f"desktop control: {'[red]ON[/]' if desktop else 'off'}   chrome: {'[yellow]ON[/]' if chrome else 'off'}")
    console.print(f"token (paste into Settings → Bridge): [bold]{token}[/]")
    console.print("kill switch: Ctrl+Alt+Esc" if killed else "[yellow]kill switch unavailable — close this window to stop")
    # Docker Desktop delivers container→host.docker.internal traffic via loopback, so 127.0.0.1 is enough.
    uvicorn.run(create_app(token, desktop, chrome, gateway_url), host="127.0.0.1", port=port, log_level="warning")
