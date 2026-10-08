"""Command cookbook (TODO N2): find the TERMINAL way to control an app — memory first, else crawl the web for it.

find_command(task, os, app):
  1. memory: a saved recipe for this task/OS → returned at once (no web, no tokens);
  2. web crawler: DuckDuckGo search + cheat.sh/tldr, crawl the top pages of trusted command-doc sites in parallel,
     pull commands out of <pre>/<code> blocks and prompt lines, rank them by overlap with the task;
  3. optional cheap model pass picks the best ones and fills in placeholders;
  4. the best commands are SAVED to memory (scope "commands") with their source, so next time step 1 answers.
The crawler only reads documentation / Q&A sites (COMMAND_SITES) — read-only GETs, any agent may use it.
"""
from __future__ import annotations

import html
import platform
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from ..clients import egress_client
from ..core.sdk import tool

SCOPE = "commands"
COMMAND_SITES = ("stackoverflow.com", "superuser.com", "serverfault.com", "askubuntu.com", "stackexchange.com",
                 "learn.microsoft.com", "docs.microsoft.com", "ss64.com", "man7.org", "manpages.ubuntu.com",
                 "linux.die.net", "cheat.sh", "tldr.sh", "github.com", "raw.githubusercontent.com", "chromium.org",
                 "developer.chrome.com", "peter.sh", "wiki.archlinux.org", "docs.python.org", "playwright.dev",
                 "linuxize.com", "howtogeek.com", "geeksforgeeks.org", "computerhope.com", "tecmint.com",
                 "cyberciti.biz", "digitalocean.com", "baeldung.com", "pypi.org", "npmjs.com")
_STOP = set("a an the to in on of for and or with my your how do i is it from using use via by at as be can".split())
_PROMPT = re.compile(r"^\s*(?:\$|PS [^>]*>|PS>|C:\\[^>]*>|>|#)\s+(.+)$")
_CMDLIKE = re.compile(r"(^|\s)(--?[A-Za-z][\w-]*|/[A-Za-z]\b)|\b(start|chrome|msedge|code|git|python|pip|npm|npx|node|"
                      r"winget|choco|powershell|pwsh|cmd|reg|wmic|tasklist|taskkill|xdg-open|open|curl|wget|ffmpeg|"
                      r"adb|docker|wsl|systemctl|apt|brew|sudo|playwright|nircmd|schtasks|netsh|explorer|code|"
                      r"Start-Process|Get-\w+|Set-\w+|Stop-Process|Invoke-\w+)\b")


def _os(os_name: str) -> str:
    o = (os_name or platform.system()).lower()
    return "windows" if o.startswith("win") else "macos" if o in ("darwin", "mac", "macos", "osx") else "linux"


def _words(s: str) -> set[str]:
    # split on every non-alphanumeric: chrome.exe → chrome, --profile-directory → profile, directory
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP and len(w) > 1}


def _allowed(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return any(h == d or h.endswith("." + d) for d in COMMAND_SITES)


def extract_commands(page: str) -> list[str]:
    """Command lines from a page: <pre>/<code> blocks and shell-prompt lines; plain-text pages line by line."""
    blocks = re.findall(r"(?is)<(?:pre|code)[^>]*>(.*?)</(?:pre|code)>", page) if "<" in page else [page]
    out: list[str] = []
    for b in blocks:
        b = html.unescape(re.sub(r"<[^>]+>", "", b))
        for line in b.splitlines():
            m = _PROMPT.match(line)
            line = (m.group(1) if m else line).strip()
            if 4 <= len(line) <= 300 and not line.startswith(("//", "#!", "<")) and _CMDLIKE.search(line) \
                    and not re.match(r"^(if|for|while|def|class|return|import|from|function|var|let|const)\b", line):
                out.append(line)
    return list(dict.fromkeys(out))


def rank(task: str, app: str, cmds: list[tuple[str, str]], os_name: str) -> list[dict]:
    want = _words(task) | _words(app)
    scored = []
    for cmd, src in cmds:
        w = _words(cmd)
        s = len(want & w) * 3 + (2 if app and app.lower() in cmd.lower() else 0)
        if os_name == "windows" and re.search(r"\b(sudo|apt|systemctl|xdg-open)\b", cmd):
            s -= 4
        if os_name != "windows" and re.search(r"\b(Start-Process|reg |wmic|\.exe\b|C:\\)", cmd):
            s -= 4
        s -= len(cmd) / 200
        scored.append({"command": cmd, "source": src, "score": round(s, 2)})
    best: dict[str, dict] = {}
    for x in sorted(scored, key=lambda x: -x["score"]):
        best.setdefault(x["command"], x)
    return list(best.values())


def _fetch(url: str) -> str:
    try:
        with egress_client(timeout=15) as c:
            r = c.get(url)
        return r.text[:400_000] if r.status_code < 400 else ""
    except Exception:  # noqa: BLE001 - one dead page never stops the crawl
        return ""


def _search(query: str, n: int = 8) -> list[str]:
    from urllib.parse import parse_qs, unquote
    try:
        with egress_client(timeout=15) as c:
            r = c.post("https://html.duckduckgo.com/html/", data={"q": query})
        urls = []
        for href in re.findall(r'class="result__a"[^>]+href="([^"]+)"', r.text):
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            urls.append(href)
        return urls[:n]
    except Exception:  # noqa: BLE001
        return []


def recall(task: str, os_name: str, k: int = 5) -> list[dict]:
    from ..memory import store
    hits = store.search(f"{task} {os_name} command", k=k * 4, scope=SCOPE)   # global memories share the index
    want = _words(task)
    out = []
    for h in hits:
        t = h.get("text", "")
        if (h.get("scope") or "") == SCOPE and f"[{os_name}]" in t and len(want & _words(t)) >= max(1, len(want) // 2):
            out.append(h)
    return out


def save(task: str, os_name: str, cmd: str, src: str, why: str = "") -> None:
    from ..memory import store
    store.add(f"[{os_name}] {task.strip()[:160]} → `{cmd}`" + (f" — {why[:160]}" if why else "") + f" (source: {src})",
              kind="fact", tags=["command", os_name], source="find_command", scope=SCOPE)


@tool("find_command", "Find the TERMINAL command to do something (control Chrome or any app, change a setting, "
      "convert a file…). Checks the saved command cookbook first, else searches + crawls the web (Stack Overflow, "
      "Microsoft Learn, man pages, cheat.sh…) and SAVES the best commands to memory for next time. Use this "
      "instead of guessing commands or writing throwaway scripts. Returns ranked commands with sources.",
      {"task": {"type": "string"}, "os": {"type": "string", "default": ""}, "app": {"type": "string", "default": ""},
       "refresh": {"type": "boolean", "default": False}})
def find_command(ctx, task: str, os: str = "", app: str = "", refresh: bool = False):
    os_name = _os(os)
    if not refresh:
        known = recall(task, os_name)
        if known:
            return {"from": "memory", "os": os_name, "commands": [h["text"] for h in known],
                    "hint": "saved recipes — run the best one with run_command and check the result"}
    queries = [f"{task} command line {os_name}", f"{app or task} {'powershell' if os_name == 'windows' else 'terminal'} command"]
    urls: list[str] = []
    for q in queries:
        urls += [u for u in _search(q) if _allowed(u)]
    for name in [app] if app else []:   # community cheat sheets: plain-text, command-only pages
        urls += [f"https://cheat.sh/{quote(name.lower())}?T", f"https://raw.githubusercontent.com/tldr-pages/tldr/main/"
                 f"pages/{'windows' if os_name == 'windows' else 'common'}/{quote(name.lower())}.md"]
    urls = list(dict.fromkeys(urls))[:8]
    with ThreadPoolExecutor(max_workers=6) as ex:
        pages = list(ex.map(lambda u: (u, _fetch(u)), urls))
    cands = [(cmd, u) for u, p in pages if p for cmd in extract_commands(p)]
    ranked = rank(task, app, cands, os_name)[:12]
    if not ranked:
        return {"from": "web", "os": os_name, "commands": [], "crawled": urls,
                "error": "no commands found — rephrase the task (name the app and the action)"}
    picked = ranked[:3]
    try:   # cheap model pass: choose + fill placeholders; ranking stays the fallback (offline/mock)
        d = ctx.llm().json(
            "Pick the 1-3 commands that best do the task on this OS. Fill placeholders with the task's values when "
            "known. Return {\"best\": [{\"command\": str, \"why\": str, \"source\": str}]}. Only use commands "
            "from the candidates (you may fill in values).",
            f"OS: {os_name}\nTask: {task}\nApp: {app}\nCandidates:\n" +
            "\n".join(f"- {x['command']}  (source {x['source']})" for x in ranked),
            fallback=lambda: {}, validate=lambda x: isinstance(x.get("best"), list) and x["best"], role="worker",
            max_tokens=500)
        picked = [b for b in d.get("best", []) if isinstance(b, dict) and b.get("command")][:3] or picked
    except Exception:  # noqa: BLE001
        pass
    for b in picked:
        save(task, os_name, b["command"], b.get("source", ""), b.get("why", ""))
    return {"from": "web", "os": os_name, "best": picked, "more": ranked[3:10], "crawled": urls,
            "saved": len(picked), "hint": "run the best command with run_command, check the result; if it was "
                                          "wrong, call again with refresh=true"}


@tool("known_commands", "Search the saved command cookbook (commands found earlier with find_command).",
      {"query": {"type": "string", "default": ""}, "k": {"type": "integer", "default": 10}})
def known_commands(ctx, query: str = "", k: int = 10):
    from ..memory import store
    hits = store.search(query or "command", k=k * 4, scope=SCOPE)
    return {"commands": [h["text"] for h in hits if (h.get("scope") or "") == SCOPE][:k]}

