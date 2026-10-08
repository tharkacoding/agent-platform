"""Site memory: what the browser agent learned about the sites it visits (PROJECT_STATE §12).

Per domain (data/site_memory/<domain>.json):
  visits, days (distinct visit dates), last_visit, pages {url_pattern: title/purpose},
  elements [{role, name, action, url_pattern, uses}]  — elements the agent successfully clicked/typed,
  paths {goal_pattern: [actions]}                      — action sequences that achieved a goal.
`hint(url)` turns that into a ≤400-char note for the decide prompt so the LLM re-plans less.

Snapshot cache (in memory): DOM snapshot per URL with a TTL (news-like sites 10 min, others 1 h). Reused when the
agent only needs to re-read a page it has not acted on since (e.g. switching back to an already-read tab).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config

DIR = config.DATA_DIR / "site_memory"
DIR.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
KNOWN_MIN_VISITS = 3
NEWS_RE = re.compile(r"(news|ycombinator|reddit|twitter|x\.com|bbc|cnn|nytimes|theguardian|reuters|feed)", re.I)
MAX_ELEMENTS, MAX_PATHS, MAX_PAGES = 40, 20, 40


def domain_of(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _file(domain: str) -> Path:
    safe = re.sub(r"[^a-z0-9.-]", "_", domain.lower())[:120] or "_"
    return DIR / f"{safe}.json"


def load(domain: str) -> dict:
    f = _file(domain)
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"domain": domain, "visits": 0, "days": [], "last_visit": 0, "pages": {}, "elements": [], "paths": {}}


def _save(d: dict) -> None:
    _file(d["domain"]).write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def url_pattern(url: str) -> str:
    """https://leetcode.com/problems/two-sum/description → /problems/*/description (ids & slugs generalised)."""
    p = urlparse(url or "")
    parts = []
    for seg in [s for s in p.path.split("/") if s][:5]:
        parts.append("*" if re.search(r"\d", seg) or len(seg) > 24 or "-" in seg else seg)
    return "/" + "/".join(parts)


def goal_pattern(goal: str) -> str:
    g = re.sub(r"https?://\S+", " ", (goal or "").lower())
    g = re.sub(r"\d+", "N", g)
    words = [w for w in re.findall(r"[a-zN]+", g) if len(w) > 2 and w not in
             {"the", "and", "for", "you", "want", "please", "can", "could", "would", "chrome", "browser", "my", "with"}]
    return " ".join(words[:8])


# ---------------------------------------------------------------------------------------------- recording
def record_run(goal: str, history: list[dict], observations: list[dict] | None = None, success: bool = True) -> list[str]:
    """Called once per browser run. Updates every domain touched. Returns the domains updated."""
    touched: dict[str, dict] = {}
    today = time.strftime("%Y-%m-%d")

    def get(url: str) -> dict | None:
        dom = domain_of(url)
        if not dom or dom in ("newtab", "new-tab-page"):
            return None
        if dom not in touched:
            touched[dom] = load(dom)
        return touched[dom]

    for o in observations or []:
        url = o.get("url") or ""
        d = get(url)
        if d is not None and (len(d["pages"]) < MAX_PAGES or url_pattern(url) in d["pages"]):
            d["pages"][url_pattern(url)] = (o.get("title") or "")[:80]
    successful = []
    for h in history or []:
        a, res = h.get("action") or {}, h.get("result")
        ok = not (isinstance(res, dict) and res.get("error")) and "error" not in str(res)[:40].lower()
        url = (res.get("url") if isinstance(res, dict) else "") or a.get("url") or ""
        d = get(url)
        if d is None or not ok:
            continue
        successful.append({k: v for k, v in a.items() if k in ("action", "url", "ref_name", "role", "key", "text")
                           and not (k == "text" and len(str(v)) > 60)})
        name = a.get("ref_name") or a.get("name")
        if a.get("action") in ("click", "type", "select") and name:
            key = (a.get("action"), str(name)[:60], a.get("role", ""))
            for el in d["elements"]:
                if (el["action"], el["name"], el.get("role", "")) == key:
                    el["uses"] += 1
                    break
            else:
                d["elements"].append({"action": key[0], "name": key[1], "role": key[2],
                                      "url_pattern": url_pattern(url), "uses": 1})
            d["elements"] = sorted(d["elements"], key=lambda e: -e["uses"])[:MAX_ELEMENTS]
    for d in touched.values():
        d["visits"] += 1
        d["last_visit"] = time.time()
        if today not in d["days"]:
            d["days"] = (d["days"] + [today])[-60:]
        gp = goal_pattern(goal)
        if success and successful and gp:
            d["paths"][gp] = successful[:12]
            if len(d["paths"]) > MAX_PATHS:
                for k in list(d["paths"])[:-MAX_PATHS]:
                    d["paths"].pop(k, None)
    with _lock:
        for d in touched.values():
            _save(d)
    return list(touched)


# ---------------------------------------------------------------------------------------------- hints
def hint(url: str, goal: str = "", limit: int = 400) -> str:
    dom = domain_of(url)
    if not dom or not _file(dom).is_file():
        return ""
    d = load(dom)
    bits = [f"Known site {dom} (visited {d['visits']}x)."]
    gp = goal_pattern(goal)
    if gp and gp in d["paths"]:
        steps = "; ".join(f"{s.get('action')} {s.get('ref_name') or s.get('url') or s.get('key') or ''}".strip()
                          for s in d["paths"][gp][:6])
        bits.append(f"Last time this goal worked via: {steps}.")
    top = [e for e in d["elements"] if e["uses"] >= 1][:6]
    if top:
        bits.append("Useful elements: " + ", ".join(f"{e['action']} '{e['name']}'" for e in top) + ".")
    out = " ".join(bits)
    return out[:limit]


# ---------------------------------------------------------------------------------------------- listing / delete
def list_sites() -> list[dict]:
    out = []
    for f in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"domain": d["domain"], "visits": d["visits"], "days": len(d.get("days", [])),
                    "last_visit": d.get("last_visit", 0), "elements": len(d.get("elements", [])),
                    "paths": len(d.get("paths", {})), "known": d["visits"] >= KNOWN_MIN_VISITS})
    return sorted(out, key=lambda x: -x["visits"])


def delete(domain: str) -> bool:
    f = _file(domain)
    if f.is_file():
        f.unlink()
        return True
    return False


# ---------------------------------------------------------------------------------------------- snapshot cache
_cache: dict[str, tuple[float, dict]] = {}


def ttl_for(url: str) -> float:
    return 600.0 if NEWS_RE.search(domain_of(url) or url) else 3600.0


def cache_put(url: str, snapshot: dict) -> None:
    if not url or snapshot.get("error"):
        return
    with _lock:
        if len(_cache) > 300:
            _cache.clear()
        _cache[url] = (time.time(), snapshot)


def cache_get(url: str, now: float | None = None) -> dict | None:
    hit = _cache.get(url)
    if not hit:
        return None
    ts, snap = hit
    if (now or time.time()) - ts > ttl_for(url):
        _cache.pop(url, None)
        return None
    return snap
