"""Small, single-job tools (TODO N3/N4): one reliable call for each thing agents kept improvising with throwaway code
(e.g. reading Chrome's 'Local State' to find where profile "asdf" lives). Every tool here is in tools_check."""
from __future__ import annotations

import ast
import base64
import datetime as dt
import fnmatch
import json
import math
import operator
import os
import time
from pathlib import Path

from ..clients import ServiceUnavailable, bridge, egress_client, runner
from ..core.sdk import tool


# ---------------------------------------------------------------------------------------------- Chrome profiles
def _local_state_dirs() -> list[Path]:
    """Chrome 'User Data' folders readable from here: the env override, Windows' own, and (inside the Linux test VM)
    the Windows one through /mnt/c."""
    out = []
    if os.environ.get("AAP_CHROME_USER_DATA"):
        out.append(Path(os.environ["AAP_CHROME_USER_DATA"]))
    if os.environ.get("LOCALAPPDATA"):
        out.append(Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data")
    out += sorted(Path("/mnt/c/Users").glob("*/AppData/Local/Google/Chrome/User Data")) if Path("/mnt/c/Users").exists() else []
    return [p for p in out if (p / "Local State").is_file()]


def read_profiles(user_data: Path) -> list[dict]:
    data = json.loads((user_data / "Local State").read_text(encoding="utf-8", errors="replace"))
    cache = (data.get("profile") or {}).get("info_cache") or {}
    return [{"name": v.get("name") or d, "dir": d, "email": v.get("user_name", ""), "path": str(user_data / d)}
            for d, v in sorted(cache.items()) if (user_data / d).is_dir()]


def list_chrome_profiles() -> dict:
    from .. import vmbrowser
    real, user_data, agentos, err = [], "", [], ""
    try:   # the host bridge knows the owner's Chrome best (runs on Windows)
        r = bridge("GET", "/chrome/profiles", timeout=8)
        if isinstance(r, dict) and r.get("profiles"):
            user_data = r.get("user_data", "")
            real = [{"name": p.get("name"), "dir": p.get("dir"), "email": p.get("user_name", ""),
                     "path": str(Path(user_data) / p.get("dir", "")) if user_data else ""} for p in r["profiles"]]
            agentos = r.get("agentos_profiles") or []
    except Exception as e:  # noqa: BLE001 - bridge off → read Local State directly
        err = str(e)[:120]
    if not real:
        for ud in _local_state_dirs():
            try:
                real, user_data = read_profiles(ud), str(ud)
                break
            except Exception as e:  # noqa: BLE001
                err = f"{ud}: {e}"[:160]
    vm = vmbrowser.profiles()
    for p in real:
        p["vm_logged_in"] = p["name"].lower() in vm
    out = {"profiles": real, "user_data": user_data, "vm_browser_profiles": sorted(vm), "agentos_profiles": agentos,
           "hint": "web work in a profile with vm_logged_in=true: browser_open(profile=name) — runs in the VM, no "
                   "screen. Otherwise the real profile: chrome_open_profile(profile=name, url=...) (uses the screen)."}
    if not real:
        out["note"] = "no Chrome 'Local State' found here" + (f" ({err})" if err else "")
    return out


@tool("chrome_profiles", "List the owner's Chrome profiles: display name (e.g. 'asdf'), folder ('Profile 6'), "
      "email, full path, and whether it is logged in inside the VM browser. Use this — never read Chrome's files "
      "yourself.", {})
def chrome_profiles(ctx):
    return list_chrome_profiles()


@tool("chrome_profile_path", "Folder + full path of ONE Chrome profile by its name or folder (e.g. 'asdf' → "
      "'Profile 6').", {"name": {"type": "string"}})
def chrome_profile_path(ctx, name: str):
    allp = list_chrome_profiles()
    n = name.strip().lower()
    hit = next((p for p in allp["profiles"] if n in (p["name"].lower(), p["dir"].lower())), None)
    if not hit:
        return {"error": f"no Chrome profile named '{name}'",
                "available": [f"{p['name']} ({p['dir']})" for p in allp["profiles"]]}
    return {**hit, "user_data": allp["user_data"],
            "launch": f'chrome.exe --profile-directory="{hit["dir"]}" <url>'}


# ---------------------------------------------------------------------------------------------- files
@tool("fs_find", "Find files by name pattern, recursively (e.g. pattern='*.csv', '*report*'). Searches /workspace, "
      "/shared and /mounts by default.", {"pattern": {"type": "string"}, "path": {"type": "string", "default": ""},
                                          "limit": {"type": "integer", "default": 50}})
def fs_find(ctx, pattern: str, path: str = "", limit: int = 50):
    pat = pattern if any(ch in pattern for ch in "*?[") else f"*{pattern}*"
    found, roots = [], [path] if path else ["/workspace", "/shared", "/mounts"]
    for root in roots:
        try:
            r = runner("GET", "/fs/list", params={"path": root, "depth": 8})
        except ServiceUnavailable as e:
            return {"error": str(e)}
        for e in (r.get("entries") or []) if isinstance(r, dict) else []:
            if e["type"] == "file" and fnmatch.fnmatch(e["path"].rsplit("/", 1)[-1].lower(), pat.lower()):
                found.append({"path": e["path"], "size": e["size"]})
    return {"files": found[:limit], "count": len(found), "truncated": len(found) > limit}


# ---------------------------------------------------------------------------------------------- web / APIs
@tool("http_request", "Call a web API: method GET/POST/PUT/PATCH/DELETE, optional JSON body and headers. Returns "
      "status + JSON (or text). Use it to control apps/services that have an API.",
      {"url": {"type": "string"}, "method": {"type": "string", "default": "GET"},
       "json_body": {"type": "object", "default": {}}, "headers": {"type": "object", "default": {}}},
      needs="network", risky=lambda a: (f"send a {a.get('method', '').upper()} request to {a.get('url', '')}"
                                        if (a.get("method") or "GET").upper() in ("POST", "PUT", "PATCH", "DELETE")
                                        else None))
def http_request(ctx, url: str, method: str = "GET", json_body: dict | None = None, headers: dict | None = None):
    m = (method or "GET").upper()
    with egress_client(timeout=30) as c:
        r = c.request(m, url, json=json_body or None, headers={k: str(v) for k, v in (headers or {}).items()})
    try:
        body = r.json()
    except ValueError:
        body = r.text[:20000]
    return {"status": r.status_code, "ok": r.status_code < 400, "url": str(r.url), "body": body}


@tool("download_file", "Download a URL into /workspace (max 20 MB). Returns the saved path.",
      {"url": {"type": "string"}, "path": {"type": "string", "default": ""}}, needs="network")
def download_file(ctx, url: str, path: str = ""):
    with egress_client(timeout=60) as c:
        r = c.get(url)
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}", "url": url}
    if len(r.content) > 20_000_000:
        return {"error": "file larger than 20 MB"}
    name = path or "/workspace/downloads/" + (url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "download.bin")
    try:
        w = runner("POST", "/fs/write_b64", json={"path": name, "data_b64": base64.b64encode(r.content).decode()})
    except ServiceUnavailable as e:
        return {"error": str(e)}
    if isinstance(w, dict) and w.get("ok"):
        ctx.files_created.append(w.get("path", name))
    return w


@tool("page_text", "Readable text + title + URL of the page open in this task's browser (one call).",
      {"max_chars": {"type": "integer", "default": 8000}}, needs="browser")
def page_text(ctx, max_chars: int = 8000):
    from .browser import browser_snapshot
    s = browser_snapshot(ctx)
    if not isinstance(s, dict) or s.get("error"):
        return s
    return {"url": s.get("url", ""), "title": s.get("title", ""), "text": str(s.get("text", ""))[:max_chars],
            "tabs": s.get("tabs", [])}


# ---------------------------------------------------------------------------------------------- LangSmith traces
@tool("langsmith_runs", "Recent AgentOS runs recorded in LangSmith (agent, status, error, seconds, tokens, trace id) — "
      "look at what failed or was slow before proposing prompt/graph changes. Needs tracing on (Settings → Tracing).",
      {"limit": {"type": "integer", "default": 10}, "errors_only": {"type": "boolean", "default": False}})
def langsmith_runs(ctx, limit: int = 10, errors_only: bool = False):
    from .. import tracing
    return tracing.recent_runs(limit, errors_only)


# ---------------------------------------------------------------------------------------------- time / math
@tool("now", "Current local date, time, weekday, timezone and unix time.", {})
def now(ctx):
    t = dt.datetime.now().astimezone()
    return {"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M:%S"), "weekday": t.strftime("%A"),
            "timezone": t.tzname(), "utc_offset": t.strftime("%z"), "iso": t.isoformat(timespec="seconds"),
            "unix": int(time.time())}


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "floor", "ceil",
                                         "factorial", "gcd", "comb", "perm")} | {"abs": abs, "round": round,
                                                                                  "min": min, "max": max}


def safe_eval(expr: str):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 10_000:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, ast.Name) and n.id in ("pi", "e", "tau"):
            return getattr(math, n.id)
        raise ValueError(f"not allowed: {ast.dump(n)[:60]}")
    return ev(ast.parse(expr.replace("^", "**"), mode="eval").body)


@tool("calc", "Evaluate a math expression exactly (e.g. '2*(3+4)', 'sqrt(2)', '15% of 80' as '0.15*80').",
      {"expression": {"type": "string"}})
def calc(ctx, expression: str):
    try:
        return {"expression": expression, "result": safe_eval(expression)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"cannot evaluate: {e}"}


# ---------------------------------------------------------------------------------------------- extension profiles
@tool("chrome_extension_profiles", "Chrome profiles whose AgentOS extension is connected (or configured): name, email, "
      "connected, wanted (the owner allowed AgentOS to use it), allowlist. browser_open(profile=<name>) works inside a "
      "profile only when connected AND wanted.", {})
def chrome_extension_profiles(ctx):
    from .. import extension
    ps = extension.profiles_status()
    return {"profiles": [{k: p[k] for k in ("profile", "email", "connected", "wanted", "allowlist", "tabs")} for p in ps],
            "usable": [p["profile"] for p in ps if p["connected"] and p["wanted"]],
            "hint": "usable profiles: browser_open(profile=name) drives their own tabs and logins; others need the "
                    "owner to switch 'Allow AgentOS to use this profile' on (Browser page)."}
