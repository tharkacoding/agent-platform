"""Site aliases that AgentOS LEARNS by itself (owner, 2026-10-08: "make site aliases on its own").

A small built-in seed (graphs/browser._SITE_ALIASES) + every site the agents actually visit: visiting
https://leetcode.com/problems/x teaches 'leetcode' → https://leetcode.com, so "open leetcode", the router
("act on a site" → browser task) and the self-learning lessons (post-mortems are keyed by domain) work for any site
the platform has ever used — without anyone editing code. Stored in data/site_aliases.json.
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import urlparse

from . import config

_FILE = config.DATA_DIR / "site_aliases.json"
_lock = threading.Lock()
_cache: dict | None = None
_SKIP = {"www", "m", "mobile", "app", "accounts", "login", "auth", "api", "cdn", "static", "mail", "localhost"}


def _seed() -> dict[str, str]:
    from .graphs.browser import _SITE_ALIASES
    return dict(_SITE_ALIASES)


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def all_aliases() -> dict[str, str]:
    return {**_seed(), **_load()}


def name_of(url: str) -> str:
    """https://www.leetcode.com/problems/x → 'leetcode' ('' for IPs/localhost/odd hosts)."""
    host = (urlparse(url).hostname or "").lower()
    if not host or re.fullmatch(r"[\d.]+", host) or host == "localhost":
        return ""
    parts = [p for p in host.split(".") if p not in _SKIP]
    if len(parts) < 2:
        return ""
    core = parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "ac", "gov") else parts[-2]
    return core if re.fullmatch(r"[a-z0-9-]{2,40}", core) else ""


def learn(url: str) -> str:
    """Remember the site of a page the agents really visited (called on every observation). Returns the name."""
    name = name_of(url)
    if not name or name in _seed():
        return name
    host = urlparse(url).hostname or ""
    base = f"https://{host}"
    with _lock:
        cur = _load()
        if cur.get(name) != base and name not in cur:
            cur[name] = base
            try:
                _FILE.parent.mkdir(parents=True, exist_ok=True)
                _FILE.write_text(json.dumps(cur, indent=1, sort_keys=True), encoding="utf-8")
            except OSError:
                pass
    return name


def resolve(word: str) -> str | None:
    return all_aliases().get((word or "").strip().lower())


def names_in(text: str) -> list[str]:
    low = (text or "").lower()
    return [n for n in all_aliases() if re.search(rf"\b{re.escape(n)}\b", low)]


def domains_in(text: str) -> list[str]:
    """Domains the text refers to — by URL or by a known (seeded or learned) site name."""
    out = [(urlparse(u).hostname or "").removeprefix("www.") for u in re.findall(r"https?://[^\s)\"'>]+", text or "")]
    out += [(urlparse(all_aliases()[n]).hostname or "").removeprefix("www.") for n in names_in(text)]
    return list(dict.fromkeys(d for d in out if d))
