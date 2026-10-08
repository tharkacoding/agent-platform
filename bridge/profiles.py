"""List the owner's real Chrome profiles and import (COPY) one into a dedicated AgentOS profile folder.

The source profile is only ever READ. Chrome must not be using it while we copy (its databases would be torn and the
cookie DB is locked), so the import refuses while Chrome runs on its default User Data folder.

Layout produced: <dest_user_data>/Local State  +  <dest_user_data>/Default/<login-relevant files>
so launching Chrome with --user-data-dir=<dest_user_data> opens the copied profile as "Default".
Note: since Chrome 127, cookies use app-bound encryption tied to chrome.exe + the Windows user; copies work for the
same Windows user and the same Chrome install. Some sites may still ask to log in again.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import security

# files/folders inside a profile that carry logins & site state (everything else — caches, history — is skipped)
PROFILE_ITEMS = ["Cookies", "Cookies-journal", "Network/Cookies", "Network/Cookies-journal", "Login Data",
                 "Login Data-journal", "Login Data For Account", "Web Data", "Preferences", "Secure Preferences",
                 "Local Storage", "IndexedDB", "Session Storage", "Extension Cookies", "Account Web Data"]


class ImportRefused(ValueError):
    pass


def chrome_user_data() -> Path:
    return security.default_chrome_dirs()[0]


def list_profiles(user_data: Path | None = None, running: Callable[[], bool] | None = None) -> dict:
    """Profiles from 'Local State' → [{dir, name, user_name}] plus whether Chrome currently uses that folder."""
    ud = user_data or chrome_user_data()
    ls = ud / "Local State"
    if not ls.is_file():
        return {"user_data": str(ud), "profiles": [], "in_use": False, "error": "Chrome 'Local State' not found"}
    try:
        data = json.loads(ls.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return {"user_data": str(ud), "profiles": [], "in_use": False, "error": f"bad Local State: {e}"}
    cache = (data.get("profile") or {}).get("info_cache") or {}
    profiles = []
    for d, info in sorted(cache.items()):
        if not (ud / d).is_dir():
            continue
        profiles.append({"dir": d, "name": info.get("name") or d, "user_name": info.get("user_name", ""),
                         "gaia_name": info.get("gaia_name", "")})
    return {"user_data": str(ud), "profiles": profiles, "in_use": in_use(ud, running)}


def default_chrome_running() -> bool:
    """True if a chrome.exe runs WITHOUT --user-data-dir (i.e. on the owner's normal profiles)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        line = line.strip()
        if line and "--type=" not in line and "--user-data-dir" not in line:
            return True
    return False


def in_use(user_data: Path, running: Callable[[], bool] | None = None) -> bool:
    return (user_data / "lockfile").exists() and (running or default_chrome_running)()


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name or "imported").strip("-.") or "imported"


def import_profile(profile_dir: str, name: str, user_data: Path | None = None, dest_base: Path | None = None,
                   running: Callable[[], bool] | None = None) -> dict:
    ud = user_data or chrome_user_data()
    src = ud / profile_dir
    if not profile_dir or "/" in profile_dir or "\\" in profile_dir or ".." in profile_dir:
        raise ImportRefused("profile_dir must be a profile folder name like 'Default' or 'Profile 1'")
    if not src.is_dir():
        raise ImportRefused(f"profile folder not found: {src}")
    if in_use(ud, running):
        raise ImportRefused("Chrome is running on your normal profiles — close ALL Chrome windows (check the tray), "
                            "then import again. The profile is copied while Chrome is closed so nothing is corrupted.")
    dest_ud = (dest_base or security.PROFILES_DIR) / _safe_name(name)
    # the destination must never be inside Chrome's own folders
    security.resolve_profile_dir(str(dest_ud), None)
    dest = dest_ud / "Default"
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    if (ud / "Local State").is_file():
        shutil.copy2(ud / "Local State", dest_ud / "Local State")   # holds the cookie/password decryption key
        copied.append("Local State")
    for item in PROFILE_ITEMS:
        s = src / item
        d = dest / item
        try:
            if s.is_dir():
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d, ignore=shutil.ignore_patterns("LOCK", "*.lock"))
                copied.append(item)
            elif s.is_file():
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
                copied.append(item)
        except OSError as e:   # a locked file means Chrome is still running
            skipped.append(f"{item}: {e.strerror or e}")
    if skipped and not copied:
        raise ImportRefused("could not read the profile files (is Chrome still running?): " + "; ".join(skipped))
    import json as _json
    (dest_ud / "imported_from.json").write_text(_json.dumps({"source": str(src), "copied": copied}),
                                                encoding="utf-8")   # marks this copy as holding real logins
    return {"ok": True, "name": dest_ud.name, "user_data_dir": str(dest_ud), "source": str(src),
            "copied": copied, "skipped": skipped}
