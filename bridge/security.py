"""Bridge token, Chrome profile path policy, and approval checks."""
from __future__ import annotations

import ctypes
import hmac
import os
import re
import secrets
import threading
from pathlib import Path

import httpx

from cli.paths import APPDATA, ensure_appdata

TOKEN_FILE = APPDATA / "bridge_token"
PROFILES_DIR = APPDATA / "chrome-profiles"


# ---------------------------------------------------------------------------------------------- token
def load_or_create_token(rotate: bool = False, path: Path | None = None) -> str:
    path = path or TOKEN_FILE
    if not rotate and path.exists():
        tok = path.read_text(encoding="utf-8").strip()
        if len(tok) >= 32:
            return tok
    ensure_appdata()
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    path.write_text(tok, encoding="utf-8")
    return tok


def token_ok(expected: str, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(expected.encode(), given.encode())


# ---------------------------------------------------------------------------------------------- chrome profiles
def default_chrome_dirs() -> list[Path]:
    """Chrome's own User Data folders. Recent Chrome refuses remote debugging on these, and we never want an
    agent attached to the profile you browse with day to day — so they are always refused."""
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return [local / "Google" / "Chrome" / "User Data",
            local / "Google" / "Chrome Beta" / "User Data",
            local / "Google" / "Chrome Dev" / "User Data",
            local / "Google" / "Chrome SxS" / "User Data",
            local / "Chromium" / "User Data"]


class ProfileRefused(ValueError):
    pass


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.expanduser().resolve(strict=False)))


def resolve_profile_dir(profile_dir: str | None, profile_name: str | None,
                        protected: list[Path] | None = None, base: Path | None = None) -> Path:
    """Return the user-data-dir to launch Chrome with, refusing Chrome's default User Data dirs (or anything
    inside them). Default: %APPDATA%\\AgentOS\\chrome-profiles\\<profile_name>."""
    protected = protected if protected is not None else default_chrome_dirs()
    if profile_dir:
        target = Path(profile_dir)
    else:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", profile_name or "default").strip("-.") or "default"
        target = (base or PROFILES_DIR) / name
    t = _norm(target)
    for d in protected:
        d_n = _norm(d)
        if t == d_n or t.startswith(d_n.rstrip("\\/") + os.sep):
            raise ProfileRefused(
                f"refusing Chrome's default profile folder ({d}). Use a dedicated folder (default: "
                f"{PROFILES_DIR}) or a copy of your profile.")
    return target


# ---------------------------------------------------------------------------------------------- approvals
def gateway_approved(gateway_url: str, approval_id: str, timeout: float = 5.0) -> bool:
    """True when the gateway lists `approval_id` among approved approvals."""
    if not approval_id:
        return False
    try:
        r = httpx.get(f"{gateway_url.rstrip('/')}/api/approvals", params={"status": "approved"}, timeout=timeout)
        if r.status_code != 200:
            return False
        return any(str(a.get("id")) == approval_id for a in r.json() or [])
    except (httpx.HTTPError, ValueError):
        return False


_MB_YESNO, _MB_ICONWARNING, _MB_TOPMOST, _MB_SETFOREGROUND = 0x4, 0x30, 0x40000, 0x10000
_IDYES = 6
_box_lock = threading.Lock()


def local_confirm(title: str, text: str, timeout_ms: int = 60000) -> bool:
    """Blocking Windows Yes/No box; auto-denies after timeout. Non-Windows → deny."""
    if os.name != "nt":
        return False
    flags = _MB_YESNO | _MB_ICONWARNING | _MB_TOPMOST | _MB_SETFOREGROUND
    with _box_lock:
        try:
            fn = ctypes.windll.user32.MessageBoxTimeoutW  # undocumented but stable since XP
            fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_ushort,
                           ctypes.c_uint]
            res = fn(None, text, title, flags, 0, timeout_ms)
        except AttributeError:
            res = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    return res == _IDYES
