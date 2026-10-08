"""Tool self-check (TODO N3): call EVERY tool with a safe sample input and report which really work.

Read-only / scratch-only samples (files under /tmp/aos_toolcheck, example.com, a throwaway browser session). Tools
that act on the owner's screen or change real things (desktop clicks, git commit, phone push, disk clean, creating
tools/skills) are listed as skipped with the reason — never run here. Custom (agent-made) tools run with their own
saved test_input. API: GET /api/tools/check · Tools page "Check all tools" · nightly self-test case 16.37.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .core.sdk import TOOLS

T = "/tmp/aos_toolcheck"
SKIP = {
    "desktop_act": "acts on the owner's screen", "desktop_locate": "needs a target on the owner's screen",
    "desktop_wait_for": "waits on the owner's screen", "chrome_open_profile": "opens a window on the owner's screen",
    "git_commit": "changes a repo", "git_branch": "changes a repo", "phone_update": "pushes to the owner's phone",
    "disk_clean": "deletes files", "disk_scan": "scans the whole disk (slow)", "create_tool": "creates a tool",
    "create_skill": "creates a skill", "memory_remember": "writes long-term memory", "custom_logic": "agent-specific",
    "browser_act": "checked in the browser sequence", "browser_open": "checked in the browser sequence",
    "browser_snapshot": "checked in the browser sequence", "browser_screenshot": "checked in the browser sequence",
    "page_text": "checked in the browser sequence",
    "langsmith_runs": "needs the owner's LangSmith API key",
}

# name → (args, ok(result) -> bool); files first (later samples read them)
SAMPLES: list[tuple[str, dict, Callable[[Any], bool]]] = [
    ("fs_write", {"path": f"{T}/a.txt", "content": "toolcheck alpha\n"}, lambda r: r.get("ok")),
    ("fs_read", {"path": f"{T}/a.txt"}, lambda r: "alpha" in str(r)),
    ("fs_patch", {"path": f"{T}/a.txt", "old": "alpha", "new": "beta"}, lambda r: r.get("ok")),
    ("fs_list", {"path": T, "depth": 1}, lambda r: any("a.txt" in e["path"] for e in r.get("entries", []))),
    ("fs_find", {"pattern": "a.txt", "path": T}, lambda r: r.get("count", 0) >= 1),
    ("search_code", {"pattern": "beta", "path": T}, lambda r: "beta" in str(r)),
    ("fs_write", {"path": f"{T}/s.csv", "content": "name,score\nom,90\nasha,80\n"}, lambda r: r.get("ok")),
    ("data_profile", {"path_or_text": f"{T}/s.csv"}, lambda r: "score" in str(r) and not r.get("error")),
    ("data_query", {"path": f"{T}/s.csv", "code": "result = df['score'].mean()"}, lambda r: "85" in str(r)),
    ("run_command", {"cmd": "echo toolcheck-ok", "cwd": T}, lambda r: "toolcheck-ok" in str(r)),
    ("run_python", {"code": "output = sum(range(10))"}, lambda r: "45" in str(r)),
    ("git_status", {"repo": T}, lambda r: isinstance(r, dict)),        # not a repo → a clean error is still "works"
    ("git_log", {"repo": T, "n": 1}, lambda r: isinstance(r, dict)),
    ("git_diff", {"repo": T}, lambda r: isinstance(r, dict)),
    ("now", {}, lambda r: len(r.get("date", "")) == 10),
    ("calc", {"expression": "2*(3+4)"}, lambda r: r.get("result") == 14),
    ("chrome_profiles", {}, lambda r: "profiles" in r),
    ("chrome_extension_profiles", {}, lambda r: "profiles" in r and "usable" in r),
    ("chrome_profile_path", {"name": "$first_profile"}, lambda r: bool(r.get("path"))),
    ("skill_read", {"skill_id": "$first_skill"}, lambda r: not r.get("error")),
    ("web_search", {"query": "example domain iana"}, lambda r: len(r.get("results", [])) >= 1),
    ("web_fetch", {"url": "https://example.com"}, lambda r: r.get("status") == 200 and "Example" in r.get("title", "")),
    ("http_request", {"url": "https://example.com"}, lambda r: r.get("status") == 200),
    ("download_file", {"url": "https://example.com", "path": f"{T}/example.html"}, lambda r: r.get("ok")),
    ("memory_recall", {"query": "toolcheck"}, lambda r: isinstance(r, (dict, list))),
    ("known_commands", {}, lambda r: "commands" in r),
    ("find_command", {"task": "list files sorted by size", "os": "linux", "app": "ls"},
     lambda r: bool(r.get("commands") or r.get("best"))),
    ("list_tools", {}, lambda r: len(str(r)) > 100),
    ("desktop_screenshot", {}, lambda r: bool(r.get("png_base64") or r.get("path"))),
    ("desktop_tree", {}, lambda r: "elements" in r),
]
DESKTOP = {"desktop_screenshot", "desktop_tree"}
WEB = {"web_search", "web_fetch", "http_request", "download_file", "find_command"}


def _resolve(args: dict) -> dict | str:
    """Samples that need something that exists here (a Chrome profile, a skill); str = skip reason."""
    out = dict(args)
    if out.get("name") == "$first_profile":
        from .tools.everyday import list_chrome_profiles
        ps = list_chrome_profiles()["profiles"]
        if not ps:
            return "no Chrome profiles on this machine"
        out["name"] = ps[0]["name"]
    if out.get("skill_id") == "$first_skill":
        from .skills import loader
        ids = [s["id"] for s in loader.list_skills()]
        if not ids:
            return "no skills installed"
        out["skill_id"] = ids[0]
    return out


class _Run:
    def __init__(self):
        self.run_id = "toolcheck-" + uuid.uuid4().hex[:6]

    def emit(self, *a, **k): pass
    def check_cancel(self): pass


class _Ctx:
    """Just enough AgentContext for a direct tool call (no permissions/approvals: samples are safe by design)."""
    def __init__(self):
        from .agents.prebuilt.catalog import _agent
        from .models import AgentTask, Permissions
        self.run = _Run()
        self.spec = _agent("toolcheck", "Tool check", "🧪", "System", "tool self-check", [], "generic",
                           tools=sorted(TOOLS), perms=Permissions(network=["*"], shell=True, browser="sandbox"))
        self.task = AgentTask(task_id="toolcheck", agent_id="toolcheck", instruction="tool self-check",
                              run_id=self.run.run_id)
        self.files_created, self.files_modified, self.tool_log = [], [], []

    def emit(self, *a, **k): pass
    def node(self, *a, **k): pass

    def llm(self):
        from .llm import llm
        return llm


def _one(ctx, name: str, args: dict, ok: Callable[[Any], bool]) -> dict:
    t0 = time.time()
    try:
        r = TOOLS[name].fn(ctx, **args)
        good = bool(ok(r)) if isinstance(r, (dict, list)) else False
        err = r.get("error", "") if isinstance(r, dict) else ""
        return {"tool": name, "status": "ok" if good else "broken", "seconds": round(time.time() - t0, 2),
                "detail": str(err or ("" if good else str(r)[:200]))[:300]}
    except Exception as e:  # noqa: BLE001
        return {"tool": name, "status": "broken", "seconds": round(time.time() - t0, 2),
                "detail": f"{type(e).__name__}: {e}"[:300]}


def _browser(ctx) -> list[dict]:
    from .tools import browser as b
    out = []
    try:
        for name, args, ok in [("browser_open", {}, lambda r: bool(r.get("session_id"))),
                               ("browser_act", {"action": "goto", "url": "https://example.com"}, lambda r: r.get("ok")),
                               ("browser_snapshot", {}, lambda r: "Example Domain" in str(r)),
                               ("page_text", {}, lambda r: "Example" in r.get("title", "") and bool(r.get("text"))),
                               ("browser_screenshot", {}, lambda r: bool(r.get("png_base64")))]:
            out.append(_one(ctx, name, args, ok))
            if out[-1]["status"] != "ok" and name == "browser_open":
                break
    finally:
        b.close_run(ctx.run.run_id)
    return out


def run(include_web: bool = True) -> dict:
    from .clients import service_health
    ctx = _Ctx()
    health = service_health()
    desktop_up = bool(health.get("bridge"))
    results: list[dict] = []
    for name, args, ok in SAMPLES:                      # in order: later samples use earlier files
        if name not in TOOLS:
            continue
        if name in DESKTOP and not desktop_up:
            results.append({"tool": name, "status": "skipped", "detail": "desktop bridge not running here"})
            continue
        if not include_web and name in WEB:
            results.append({"tool": name, "status": "skipped", "detail": "web checks turned off for this run"})
            continue
        a = _resolve(args)
        results.append({"tool": name, "status": "skipped", "detail": a} if isinstance(a, str) else _one(ctx, name, a, ok))
    results += _browser(ctx)
    from .core import toolfactory
    with ThreadPoolExecutor(4) as ex:                   # agent-made tools: their own saved test input
        results += list(ex.map(lambda m: _one(ctx, m["name"], m.get("test_input") or {},
                                              lambda r: not (isinstance(r, dict) and r.get("error"))),
                               [m for m in toolfactory.list_custom() if m["name"] in TOOLS]))
    seen = {r["tool"] for r in results}
    results += [{"tool": n, "status": "skipped", "detail": why} for n, why in SKIP.items() if n in TOOLS and n not in seen]
    results += [{"tool": n, "status": "unchecked", "detail": "no sample input yet — add one to aap/toolcheck.py"}
                for n in sorted(TOOLS) if n not in {r["tool"] for r in results}]
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("ok", "broken", "skipped", "unchecked")}
    return {"counts": counts, "results": results, "at": time.time()}
