"""Launch the user's installed Chrome with a remote-debugging port on a NON-default profile folder."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

CDP_PORT = 9222
PUBLIC_PORT = 9223


def find_chrome() -> str | None:
    env = os.environ
    candidates = [
        Path(env.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("chrome") or shutil.which("chrome.exe")


def cdp_alive(port: int = CDP_PORT) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


# The debug-port Chrome we started: which profile it runs. Never silently reuse a Chrome on a different profile.
_active: dict = {"user_data_dir": None, "profile": None, "proc": None}


def active() -> dict:
    return {"user_data_dir": str(_active["user_data_dir"]) if _active["user_data_dir"] else None,
            "profile": _active["profile"], "alive": cdp_alive()}


def _stop_ours() -> bool:
    p = _active.get("proc")
    if p is None:
        return False
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    for _ in range(40):
        if not cdp_alive():
            break
        time.sleep(0.25)
    _active.update(user_data_dir=None, profile=None, proc=None)
    return True


def launch(user_data_dir: Path, profile_name: str | None = None) -> dict:
    want = (str(Path(user_data_dir).resolve()).lower(), profile_name or "")
    if cdp_alive():
        have = (str(Path(_active["user_data_dir"]).resolve()).lower() if _active["user_data_dir"] else None,
                _active["profile"] or "")
        if have == want:
            return {"already_running": True, "profile": profile_name, "switched": False}
        if have[0] is None:
            raise RuntimeError("A Chrome with remote debugging is already open but AgentOS doesn't know its profile. "
                               "Close that Chrome window so the requested profile can be opened.")
        _stop_ours()              # running on another profile: switch instead of silently reusing it
        switched = True
    else:
        switched = False
    exe = find_chrome()
    if not exe:
        raise FileNotFoundError("Chrome not found — install Google Chrome")
    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={user_data_dir}", "--no-first-run", "--no-default-browser-check"]
    if profile_name and (profile_name.startswith("Profile ") or profile_name == "Default"):
        args.append(f"--profile-directory={profile_name}")
    proc = subprocess.Popen(args, close_fds=True)
    _active.update(user_data_dir=user_data_dir, profile=profile_name or "", proc=proc)
    for _ in range(40):
        if cdp_alive():
            return {"already_running": False, "profile": profile_name, "switched": switched}
        time.sleep(0.25)
    raise TimeoutError("Chrome started but the DevTools port did not open")
