"""Host-side locations: %APPDATA%\\AgentOS (config, mounts, bridge token), the shared folder, the repo dir."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "AgentOS"
CONFIG_FILE = APPDATA / "config.json"
MOUNTS_FILE = APPDATA / "mounts.json"
SHARED_DIR = Path(os.environ.get("AOS_SHARED") or (Path.home() / "AgentOS" / "shared"))
GATEWAY_URL = os.environ.get("AGENTOS_URL", "http://127.0.0.1:7860").rstrip("/")
VNC_URL = "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale"


def ensure_appdata() -> Path:
    APPDATA.mkdir(parents=True, exist_ok=True)
    return APPDATA


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(patch: dict) -> dict:
    ensure_appdata()
    cfg = load_config()
    cfg.update(patch)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def _is_repo(p: Path) -> bool:
    return (p / "docker-compose.yml").exists()


def repo_dir() -> Path:
    """The folder holding docker-compose.yml.

    Frozen exe: config.json "repo_dir" (written by `agentos setup`), else the exe's folder (and its parent,
    for dist/agentos.exe inside the repo). Source run: the package's parent folder.
    """
    candidates: list[Path] = []
    cfg_dir = load_config().get("repo_dir")
    if cfg_dir:
        candidates.append(Path(cfg_dir))
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent
        candidates += [exe, exe.parent]
    candidates.append(Path(__file__).resolve().parent.parent)
    candidates.append(Path.cwd())
    for c in candidates:
        if _is_repo(c):
            return c
    return candidates[0]
