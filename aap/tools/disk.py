"""Disk cleanup tools — only well-known, SAFE-to-delete categories (caches, old temp files, old AgentOS logs/screenshots,
AgentOS test browser profiles). Never personal files, never program folders. Scan is read-only; clean always asks."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import config
from ..core.sdk import tool

HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
DAY = 86400


def _age_ok(p: Path, days: int) -> bool:
    try:
        return time.time() - p.stat().st_mtime > days * DAY
    except OSError:
        return False


def _items(cat: str) -> list[Path]:
    """Exact things a category would delete (top-level entries)."""
    if cat == "npm_cache":
        return [p for p in [LOCAL / "npm-cache", ROAMING / "npm-cache"] if p.exists()]
    if cat == "pip_cache":
        return [p for p in [LOCAL / "pip" / "Cache", LOCAL / "pip" / "cache"] if p.exists()]
    if cat == "temp_old":     # temp entries untouched for 2+ days
        tmp = Path(os.environ.get("TEMP", LOCAL / "Temp"))
        return [p for p in tmp.iterdir() if _age_ok(p, 2)] if tmp.exists() else []
    if cat == "agentos_logs":
        d = config.DATA_DIR / "logs"
        return [p for p in d.iterdir() if p.is_file() and _age_ok(p, 3)] if d.exists() else []
    if cat == "agentos_screenshots_old":
        d = HOME / "AgentOS" / "shared" / "screenshots"
        return [p for p in d.iterdir() if p.is_file() and _age_ok(p, 7)] if d.exists() else []
    if cat == "agentos_test_browser_profiles":   # sandbox profiles made by tests (never the user's real Chrome)
        out = []
        for root in (config.DATA_DIR / "browser_profiles", ROAMING / "AgentOS" / "chrome-profiles"):
            if root.exists():
                out += [p for p in root.iterdir() if p.is_dir() and p.name.lower() in
                        {"tabtest", "codetest", "e2e", "test", "qa"}]
        return out
    if cat == "python_bytecode":
        return [p for p in Path(config.ROOT).rglob("__pycache__") if "node_modules" not in p.parts]
    if cat == "build_artifacts":     # rebuildable: PyInstaller work dir
        return [p for p in [Path(config.ROOT) / "build"] if p.exists()]
    return []


CATEGORIES = {
    "npm_cache": "npm download cache (re-downloaded when needed)",
    "pip_cache": "pip download cache",
    "temp_old": "Windows temp files older than 2 days",
    "agentos_logs": "AgentOS logs older than 3 days",
    "agentos_screenshots_old": "AgentOS screenshots older than 7 days",
    "agentos_test_browser_profiles": "browser profiles created by AgentOS tests",
    "python_bytecode": "AgentOS __pycache__ folders",
    "build_artifacts": "AgentOS exe build work folder (rebuildable)",
}


def _size(p: Path) -> int:
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@tool("disk_scan", "READ-ONLY: how much space each safe-to-clean category uses (caches, old temp files, old AgentOS "
      "logs/screenshots, test browser profiles), plus free disk space. Use before disk_clean.", {})
def disk_scan(ctx):
    out = []
    for cat, desc in CATEGORIES.items():
        items = _items(cat)
        out.append({"category": cat, "what": desc, "items": len(items),
                    "gb": round(sum(_size(p) for p in items) / 1e9, 2)})
    free = shutil.disk_usage(HOME.anchor).free / 1e9
    return {"free_gb": round(free, 1), "categories": sorted(out, key=lambda x: -x["gb"]),
            "not_touched": "your files, Docker, programs, real Chrome profiles"}


def _clean_risk(args: dict):
    return f"DELETE files to free space — categories: {', '.join(args.get('categories') or [])}"   # always asks


@tool("disk_clean", "Delete ONLY the given safe categories from disk_scan (asks the user first). Returns freed GB.",
      {"categories": {"type": "array", "items": {"type": "string"}}}, risky=_clean_risk)
def disk_clean(ctx, categories: list):
    freed, errors, done = 0, 0, []
    for cat in categories or []:
        if cat not in CATEGORIES:
            continue
        if cat == "npm_cache" and shutil.which("npm"):
            subprocess.run(["npm", "cache", "clean", "--force"], shell=True, capture_output=True, timeout=300)
        for p in _items(cat):
            s = _size(p)
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                freed += s
            except OSError:
                errors += 1            # in use / locked: skipped
        done.append(cat)
    return {"cleaned": done, "freed_gb": round(freed / 1e9, 2), "skipped_locked": errors,
            "free_gb_now": round(shutil.disk_usage(HOME.anchor).free / 1e9, 1)}
