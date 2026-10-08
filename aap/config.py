"""Static configuration (paths, service URLs, limits). Runtime-editable values live in settings.py.

Inside Docker the gateway uses /data; locally (dev/tests) it uses ./data. Override with AAP_* env vars.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"AAP_{name}", default)


DATA_DIR = Path(_env("DATA_DIR", "/data" if Path("/.dockerenv").exists() else str(ROOT / "data")))
CUSTOM_AGENTS_DIR = DATA_DIR / "custom_agents"
SKILLS_DIR = DATA_DIR / "skills"
MEMORY_MD_DIR = DATA_DIR / "memory"
SOULS_DIR = DATA_DIR / "souls"                   # souls/<id>/SOUL.md (selectable personalities)
WORKSPACE_MD_DIR = DATA_DIR / "workspace_md"     # AGENTS/USER/IDENTITY/TOOLS/HEARTBEAT/MEMORY.md
DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "registry.db")))
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"
WEB_DIST = Path(_env("WEB_DIST", str(ROOT / "web" / "dist")))

# Sandboxed services (docker-compose service names). Empty -> service unavailable (dev mode).
RUNNER_URL = _env("RUNNER_URL", "http://runner:8001" if Path("/.dockerenv").exists() else "")
BROWSER_URL = _env("BROWSER_URL", "http://browser:8002" if Path("/.dockerenv").exists() else "")
VNC_PUBLIC_URL = _env("VNC_PUBLIC_URL", "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale")
IN_DOCKER = Path("/.dockerenv").exists()
# Host mode (dev, no Docker): the runner executes directly on this machine — NO sandbox. Tools become much stricter.
RUNNER_ON_HOST = any(h in RUNNER_URL for h in ("://127.0.0.1", "://localhost")) if RUNNER_URL else False
BRIDGE_URL = _env("BRIDGE_URL", "http://host.docker.internal:8765" if IN_DOCKER else "http://127.0.0.1:8765")
# who attaches to host Chrome over CDP: the browser container ("docker") or a dev-mode local browser_svc ("local")
BRIDGE_CDP_PUBLIC = _env("BRIDGE_CDP_PUBLIC", "docker" if IN_DOCKER else "local")

# Defaults for settings.py (editable in the UI)
LLM_PROVIDER = _env("LLM_PROVIDER", "auto")      # auto | openai | mock
LLM_BASE_URL = _env("LLM_BASE_URL", "http://host.docker.internal:20128/v1" if Path("/.dockerenv").exists()
                    else "http://localhost:20128/v1")
LLM_API_KEY = _env("LLM_API_KEY", "none")
LLM_MODEL = _env("LLM_MODEL", "")
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "180"))
REQUIRE_APPROVAL = _env("REQUIRE_APPROVAL", "false").lower() == "true"
NETWORK_ALLOWLIST = [h for h in _env("NETWORK_ALLOWLIST", "").split(",") if h]

# Limits
MAX_DELEGATION_DEPTH = int(_env("MAX_DELEGATION_DEPTH", "3"))
MAX_BUILD_ATTEMPTS = int(_env("MAX_BUILD_ATTEMPTS", "3"))
MAX_REVIEW_LOOPS = int(_env("MAX_REVIEW_LOOPS", "2"))
MAX_PARALLEL_AGENTS = int(_env("MAX_PARALLEL_AGENTS", "4"))
APPROVAL_TIMEOUT = float(_env("APPROVAL_TIMEOUT", "900"))

# Filesystem roots agents may address (container paths, enforced again by the runner)
FS_ROOTS = ["/workspace", "/shared", "/mounts", "/tmp"]

for _d in (DATA_DIR, CUSTOM_AGENTS_DIR, SKILLS_DIR, MEMORY_MD_DIR, SOULS_DIR, WORKSPACE_MD_DIR):
    _d.mkdir(parents=True, exist_ok=True)
