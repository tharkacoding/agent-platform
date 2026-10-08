"""Runtime-editable settings (endpoint / key / per-role models / channels ...), persisted to data/settings.json.

The UI Settings page and `agentos setup` edit these, so the app works with OmniRoute or any OpenAI-compatible
endpoint without a restart.
"""
from __future__ import annotations

import json
import threading

from . import config

_PATH = config.DATA_DIR / "settings.json"
_lock = threading.Lock()
ROLES = ["main", "coder", "builder", "worker", "vision", "embed"]
SECRET_KEYS = {"langsmith_api_key", "llm_api_key", "telegram_token", "discord_token", "bridge_token", "search_api_key", "extension_token"}

DEFAULTS: dict = {
    "llm_provider": config.LLM_PROVIDER,      # auto | openai | mock
    "llm_base_url": config.LLM_BASE_URL,
    "llm_api_key": config.LLM_API_KEY,
    "llm_model": config.LLM_MODEL,            # default model for every role
    "llm_timeout": config.LLM_TIMEOUT,
    "role_models": {r: "" for r in ROLES},    # per-role override; "" -> llm_model
    "require_approval": config.REQUIRE_APPROVAL,
    "network_allowlist": ",".join(config.NETWORK_ALLOWLIST),
    "telegram_token": "",
    "telegram_user_id": "",
    "discord_token": "",
    "discord_user_id": "",
    "bridge_token": "",
    "browser_default_mode": "sandbox",        # sandbox (container Chromium) | host (your Chrome via the bridge)
    "langsmith_enabled": False,               # trace every agent step + model call to LangSmith (aap/tracing.py)
    "langsmith_api_key": "",
    "langsmith_project": "agentos",
    "langsmith_prompts": False,               # use agent prompts edited in LangSmith's Prompt Hub (agentos-<agent>)
    "superpowers_enabled": True,              # agents get the Superpowers methodology index (aap/skills/methodology)
    "mascot_enabled": True,                   # UI: page-mascot fox while a task/check runs (Settings → Appearance)
    "vm_browser_url": "http://127.0.0.1:8012",   # the test VM's browser (scripts/vm_up.sh); "" = off (aap/vmbrowser)
    "vm_view_url": "http://localhost:6081/vnc.html?autoconnect=1&resize=scale",   # watch the VM screen (noVNC)
    "browser_profile": "default",             # host: %APPDATA%\AgentOS\chrome-profiles\<name> (dedicated profile)
    "extension_token": "",                    # pairing token for the AgentOS Chrome extension (browser mode "extension")
    "extension_allowlist": "",                # domains the extension may act on without asking (others: read-only)
    # per Chrome profile (one extension install each): {"asdf": {"wanted": true, "allowlist": "leetcode.com"}}.
    # wanted = the owner allows AgentOS to use that profile through its extension (default: off); allowlist adds to
    # the global one above. Edited on the Browser page (PUT /api/extension/profile).
    "extension_profiles": {},
    "search_provider": "duckduckgo",          # duckduckgo | tavily
    "search_api_key": "",
    "default_soul": "default",                # soul used when a conversation has none (aap/souls)
    # Autonomy rules (precedence never > ask > allow). pattern = fnmatch on "<agent_id>:<tool>:<arg summary>",
    # e.g. {"pattern": "*:run_command:pytest*", "policy": "allow"}, {"pattern": "*:git_commit:*", "policy": "ask"}
    "autonomy_rules": [],
    "notify_enabled": False,                  # master on/off switch for phone pushes (Settings → Notifications)
    "ntfy_inbound": True,                     # phone → AgentOS ('claude …' → Claude Code) via the ntfy topic
    "notify_ntfy_url": "https://ntfy.sh/omreddyenduri",   # phone push (ntfy) when a task ends / needs you; '' = off
    "notify_on": "all",                       # all (finished/failed/stopped/waiting for you) | finish | off
    "notify_priority": "high",                # min | low | default | high | urgent
    "token_budget_mode": "off",   # off = never stop work for tokens (tiers only pick models) | enforce = hard cap
    "auto_mode": True,        # approve automatically (dangerous actions still ask; no screen control while you use the PC)
    "laptop_access": "read",  # your home folder as /laptop for agents: off | read (always read-only)
    # Dynamic model routing by role + seriousness tier with cross-provider failover (aap/routing.py).
    # chains: optional overrides, keys like "general:cheap", "coding:deep", "vision:any" -> [model ids]
    "routing": {"enabled": True, "chains": {}},
}

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        data = json.loads(json.dumps(DEFAULTS))
        if _PATH.exists():
            try:
                data.update(json.loads(_PATH.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - corrupt file: defaults
                pass
        _cache = data
    return _cache


def get(key: str):
    with _lock:
        return _load().get(key, DEFAULTS.get(key))


def _mask(v: str) -> str:
    if not v or v == "none":
        return v
    return v[:3] + "…" + v[-3:] if len(v) > 8 else "set"


def all_settings(mask: bool = True) -> dict:
    with _lock:
        d = json.loads(json.dumps(_load()))
    if mask:
        for k in SECRET_KEYS:
            d[k] = _mask(d.get(k, ""))
    return d


def update(patch: dict) -> dict:
    with _lock:
        d = _load()
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k in SECRET_KEYS and isinstance(v, str) and ("…" in v or v == "set"):
                continue  # masked value echoed back by the UI: keep the real one
            if k == "role_models" and isinstance(v, dict):
                d.setdefault("role_models", {}).update({r: v.get(r, "") for r in ROLES if r in v})
                continue
            d[k] = v
        _PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    if any(k.startswith("langsmith_") for k in patch):
        try:
            from . import tracing
            tracing.apply()
        except Exception:  # noqa: BLE001 - tracing must never break saving settings
            pass
    return all_settings()


def model_for(role: str) -> str:
    return (get("role_models") or {}).get(role) or get("llm_model") or ""


def network_allowlist() -> list[str]:
    return [h.strip() for h in str(get("network_allowlist") or "").split(",") if h.strip()]
