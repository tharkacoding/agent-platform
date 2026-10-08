"""Browser and Desktop agents — observe → decide → (sensitive gate) → act → verify loop.

`browser`: Playwright in the sandboxed browser container (or the user's Chrome over CDP when permitted).
`desktop`: Windows desktop via the Host Bridge (UI Automation tree); every action is approval-gated.
"""
from __future__ import annotations

import json
import re
import time

from langgraph.graph import END, START, StateGraph

from ..core.sdk import ApprovalDenied, archetype, get_ctx
from . import _common as c

MAX_STEPS = 15
SCREENSHOT_ONLY = re.compile(r"(?i)\b(screenshot|screen ?shot|screen capture|capture (my|the) screen|picture of (my|the) "
                             r"screen|what'?s on my screen|show me my screen)\b")
ACTION_WORDS = re.compile(r"(?i)\b(click|type|open|launch|press|close|write|fill|drag|scroll|double[- ]click)\b")
SENSITIVE = re.compile(r"\b(buy|purchase|checkout|pay|payment|order now|place order|send|post|publish|submit|delete|"
                       r"remove|transfer|password|sign up|subscribe|confirm)\b", re.I)


class UIState(c.BaseState, total=False):
    goal: str
    visual_used: bool
    session: dict
    observation: dict
    history: list
    action: dict
    done: bool
    answer: str
    steps: int
    blocked: str
    max_steps: int
    seen: list          # [{url, title}] pages observed this run (feeds site memory)
    tab_urls: list      # planned URLs for "open N tabs of <specific things>" (one LLM call, then deterministic)
    profile: str        # the browser profile this task MUST use (explicit, never "")
    xstate: dict        # shared task state (core/execstate.py) — continues across handoffs
    avoid: list         # action signatures that failed / changed nothing → never repeat them
    stalls: int         # observations that did not change after an action
    last_obs_sig: str
    verify_tries: int
    verified: bool
    evidence: str
    stalled: bool
    sup_checked: int    # step of the last Main-Agent supervisor checkpoint


def _kind(ctx) -> str:
    return "desktop" if ctx.spec.graph == "desktop" else "browser"


# ---- offline (no-LLM) goal planner: simple goals still work deterministically ---------------------------------
_SEARCH_URLS = {
    "google": "https://www.google.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "ddg": "https://duckduckgo.com/?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
}
_SITE_ALIASES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com",
                 "gmail": "https://mail.google.com", "github": "https://github.com",
                 "wikipedia": "https://en.wikipedia.org", "duckduckgo": "https://duckduckgo.com",
                 "chatgpt": "https://chatgpt.com", "codeforces": "https://codeforces.com", "cses": "https://cses.fi",
                 "leetcode": "https://leetcode.com", "amazon": "https://www.amazon.in", "reddit": "https://www.reddit.com",
                 "linkedin": "https://www.linkedin.com", "stackoverflow": "https://stackoverflow.com",
                 "spotify": "https://open.spotify.com", "netflix": "https://www.netflix.com",
                 "instagram": "https://www.instagram.com", "hackerrank": "https://www.hackerrank.com",
                 "atcoder": "https://atcoder.jp", "geeksforgeeks": "https://www.geeksforgeeks.org"}


def _site_url(site: str) -> str | None:
    s = site.strip().strip(".,;:!?\"'").lower()
    from .main import _NUM_WORDS
    if not s or s.isdigit() or s in _NUM_WORDS or s in {"new", "a", "an", "the", "my", "tab", "tabs", "more", "up",
                                                         "chrome", "browser", "it", "this", "that", "some"}:
        return None
    if s.startswith(("http://", "https://")):
        return s
    from .. import site_aliases
    if site_aliases.resolve(s):          # seeded or LEARNED from sites the agents visited before
        return site_aliases.resolve(s)
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", s):
        return "https://" + s
    if re.fullmatch(r"[a-z0-9-]{2,40}", s):
        return f"https://www.{s}.com"
    return None


def offline_plan(goal: str) -> list[str]:
    """URLs to visit for simple goals: 'open <url>', 'go to <site>', 'search <q> on google|duckduckgo|youtube'.
    'take a screenshot' needs no step (finalize always screenshots)."""
    from urllib.parse import quote_plus
    g = goal.strip()
    urls: list[str] = []
    m = re.search(r"(?i)\b(?:search|look up|find)\s+(?:for\s+)?[\"']?(.+?)[\"']?\s+(?:on|in|using)\s+"
                  r"(google|duckduckgo|ddg|bing|youtube|github|wikipedia)\b", g)
    if not m:
        m2 = re.search(r"(?i)\b(google|youtube)\s+(?:for\s+)?[\"']?(.+?)[\"']?\s*(?:$|\band\b|[.,;])", g)
        if m2 and re.match(r"(?i)(in|on|with|of|tabs?)\b", m2.group(2).strip()):
            m2 = None   # "youtube in chrome" is a site, not a search for "in chrome"
        if m2 and not re.match(r"(?i)(?:open|go|visit)", g):
            m = None
            engine, q = m2.group(1), m2.group(2)
            urls.append(_SEARCH_URLS[engine.lower()].format(q=quote_plus(q.strip())))
    if m:
        q, engine = m.group(1).strip(), m.group(2).lower()
        urls.append(_SEARCH_URLS[engine].format(q=quote_plus(q)))
    elif not urls:
        m3 = re.search(r"(?i)\bsearch\s+(?:for\s+)?[\"']?(.+?)[\"']?\s*(?:$|\band\b|[.,;])", g)
        if m3:
            urls.append(_SEARCH_URLS["duckduckgo"].format(q=quote_plus(m3.group(1).strip())))
    urls += [u.rstrip(".,;") for u in c.URL_RE.findall(g)]
    m5 = re.search(r"(?i)\btabs?\s+(?:of|with|for|on|showing)\s+([^\s,;]+)", g)   # "open 5 tabs of youtube"
    if m5 and _site_url(m5.group(1)):
        urls.append(_site_url(m5.group(1)))
    for m4 in re.finditer(r"(?i)\b(?:open|go to|goto|visit|navigate to|browse to)\s+(?:the\s+)?(?:website\s+|site\s+)?"
                          r"([^\s,;]+)", g):
        word = m4.group(1).strip(".,;:!?\"'").lower()
        if len(g) > 80 and "." not in word and word not in _SITE_ALIASES:
            continue   # long (planner-written) goals: "open in…", "go to problem…" are words, not sites (T1 went to
            #            www.in.com / www.problem.com / www.at.com) — only real URLs, domains and known sites count
        u = _site_url(m4.group(1))
        if u and u not in urls and not any(x.startswith(u) for x in urls):
            urls.append(u)
    return list(dict.fromkeys(urls))


_PASTE_RE = re.compile(r"(?i)\b(paste|put|insert|write|enter|type|set)\b.{0,60}\b(code|solution|answer)\b.{0,60}"
                       r"\b(editor|code ?box|textarea|ide)\b")


_SOLVERS = ("competitive_programming_agent", "coding_agent", "python_agent")


def solution_code(state: dict) -> str:
    """The code to paste = the code block of the newest UPSTREAM step report (solvers first). Only without reports
    fall back to the goal text — which also holds recalled memories: T1 pasted an OLD Valid-Parentheses program from
    memory into the Two Sum editor because it was the largest block in the goal."""
    reps = [r for r in ((state.get("task") or {}).get("context") or []) if isinstance(r, dict) and "agent_id" in r]
    for r in sorted(reversed(reps), key=lambda r: r.get("agent_id") not in _SOLVERS):   # newest first, solvers first
        code = extract_code(r.get("result") if isinstance(r.get("result"), str) else json.dumps(r.get("result", "")))
        if code:
            return code
    return extract_code(state.get("goal", ""))


def code_language(code: str) -> str:
    """The language a code judge's picker must show for this code ('' = unknown, leave the picker alone)."""
    if re.search(r"#include\s*<|\bstd::|\bvector<", code):
        return "C++"
    if re.search(r"\bpublic\s+(class|int|boolean|String|List)\b", code):
        return "Java"
    if re.search(r"(?m)^\s*(def|class)\s+\w+.*:\s*$", code) and not re.search(r";\s*$", code, re.M):
        return "Python3"
    if re.search(r"\bfunction\b|=>|\b(const|let)\s+\w+\s*=", code):
        return "JavaScript"
    return ""


def wrong_judge_format(code: str, goal: str) -> str:
    """'' if fine; else why this code can't be submitted (function-signature judges like LeetCode never read stdin)."""
    if re.search(r"(?i)leetcode", goal) and not re.search(r"\bclass\s+Solution\b", code) \
            and re.search(r"sys\.stdin|\binput\s*\(", code):
        return "the code reads stdin, but LeetCode calls a method of `class Solution` — it must be rewritten"
    return ""


def extract_code(text: str) -> str:
    """Largest fenced code block in the text (the solution handed over by the solving step)."""
    blocks = re.findall(r"```[a-zA-Z0-9+#]*\n(.*?)```", text, re.S)
    return max(blocks, key=len).strip("\n") if blocks else ""


def requested_mode(goal: str) -> str:
    """Explicit wording wins over Settings: 'my chrome/browser/profile' → host, 'sandbox' → sandbox, else ''."""
    low = goal.lower()
    if re.search(r"\b(this|current|my current|my open|open)\s+tabs?\b|\bextension\b", low):
        return "extension"
    if re.search(r"\b(my|own|real)\s+(chrome|browser|profile)\b", low):
        return "host"
    if re.search(r"\b(sandbox|sandboxed|isolated)\b", low):
        return "sandbox"
    return ""


def start(state: UIState, config) -> dict:
    ctx = get_ctx(config)
    kind = _kind(ctx)
    ctx.node("start", "Opening browser session" if kind == "browser" else "Connecting to the desktop bridge")
    from ..core import execstate
    xs = execstate.from_context(ctx.task.context) or execstate.new_state(c.instruction(state))
    sess, profile = {}, ""
    if kind == "browser":
        from ..tools.browser import resolve_mode, resolve_profile
        mode = resolve_mode(requested_mode(c.instruction(state)), ctx.spec.permissions.browser)
        profile, src = resolve_profile(ctx, ctx.spec.config.get("profile", ""))
        r = c.tool(ctx, "browser_open", profile=profile, mode=mode)
        sess = r if isinstance(r, dict) else {"error": str(r)}
        if sess.get("error"):   # no silent fallback to another profile/browser: stop and say why
            ctx.emit(f"Browser unavailable (profile {profile}): {sess['error']}", status="failed")
        xs["browser"].update(profile=profile, mode=sess.get("mode", mode))
        ctx.emit(f"Browser profile: {profile} (from {src})", profile=profile)
    elif kind == "desktop":
        # computer use in the user's REAL Chrome profile: open the page there, then observe THAT profile's window
        from ..tools.browser import resolve_profile
        urls = [u.rstrip(".,;)") for u in c.URL_RE.findall(c.instruction(state).split("INPUTS FROM")[0])]
        if not urls:   # the page may have been chosen by an earlier step (e.g. the solver picked the problem)
            urls = [u.rstrip(".,;)`'\"") for u in c.URL_RE.findall(c.context_text(state, 5000))][:1]
        wants_chrome = bool(urls) or re.search(r"(?i)\b(chrome|profile|browser|website|site)\b", c.instruction(state))
        from ..tools.desktop import _profile_requested
        named, src = resolve_profile(ctx)
        if wants_chrome and c.can(ctx, "chrome_open_profile") and not _profile_requested(ctx, named):
            # the owner named no Chrome profile: use THEIR default browser (never one of their profiles on our own)
            if urls:
                r = c.tool(ctx, "desktop_act", action="launch", app=urls[0])
                if isinstance(r, dict) and r.get("error"):
                    ctx.emit(f"Could not open {urls[0]} in your default browser: {r['error']}", status="failed")
            sess = {"window": "", "contains": ""}   # observe whatever browser window is in front
            xs["browser"].update(profile="(your default browser)", mode="default-browser")
            ctx.emit("No Chrome profile was named — using your default browser", profile="")
        elif wants_chrome and c.can(ctx, "chrome_open_profile"):
            profile, src = named, src
            if urls:
                r = c.tool(ctx, "chrome_open_profile", profile=profile, url=urls[0])
                sess = r if isinstance(r, dict) else {"error": str(r)}
                if sess.get("error"):
                    ctx.emit(f"Could not open your Chrome profile '{profile}': {sess['error']}", status="failed")
            sess = {**sess, "window": "Google Chrome", "contains": f"- {profile}"}
            xs["browser"].update(profile=profile, mode="real-chrome")
            ctx.emit(f"Using your real Chrome profile: {profile} (from {src})", profile=profile)
    from .. import site_memory
    xs["lessons"] = list(dict.fromkeys(xs.get("lessons", []) + execstate.lessons_for(
        [site_memory.domain_of(u) for u in offline_plan(c.instruction(state))] + [ctx.spec.id], 4)))
    goal = c.instruction(state)
    inputs = c.context_text(state, 5000)
    if inputs:   # results of the steps this one depends on (e.g. the solved code to paste)
        goal += "\n\nINPUTS FROM PREVIOUS STEPS (use these, don't redo them):\n" + inputs
    return {"goal": goal, "session": sess, "history": [], "steps": 0, "done": False,
            "max_steps": _step_budget(ctx), "profile": profile, "xstate": xs, "avoid": [], "stalls": 0,
            "verify_tries": 0, "verified": False}


def _step_budget(ctx) -> int:
    base = max(3, min(MAX_STEPS * 2, int(c.budget(ctx).get("react_steps", MAX_STEPS))))
    from .main import _batch_size   # multi-tab / multi-item goals need ~4 actions per item
    n = max(_batch_size(ctx.task.instruction), tab_count(ctx.task.instruction))
    return min(80, max(base, n * 4 + 4)) if n else base


def tab_count(goal: str) -> int:
    """'open 5 tabs' / 'open five tabs of youtube' -> 5 (0 if the goal isn't about opening N tabs)."""
    from .main import _NUM_WORDS
    head = goal.split("INPUTS FROM PREVIOUS STEPS")[0].split("## Task state")[0]
    if _PASTE_RE.search(head):          # "for each of the 5 tabs, paste…" is NOT a request to open tabs
        return 0
    m = re.search(r"(?i)\b(?:open|create|launch|start)\b(?:\s+\w+){0,3}?\s+(\d{1,2}|" + "|".join(_NUM_WORDS)
                  + r")\s+(?:new\s+|more\s+|different\s+|chrome\s+|browser\s+)*tabs?\b", head)
    if not m:
        return 0
    v = m.group(1).lower()
    n = int(v) if v.isdigit() else _NUM_WORDS[v]
    return n if 1 <= n <= 30 else 0


def observe(state: UIState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("observe", f"Observing (step {state.get('steps', 0) + 1})")
    if _kind(ctx) == "browser":
        from .. import site_memory
        last = (state.get("history") or [{}])[-1]
        last_url = (last.get("result") or {}).get("url", "") if isinstance(last.get("result"), dict) else ""
        cached = site_memory.cache_get(last_url) if last.get("action", {}).get("action") in ("tab_switch", "wait") \
            else None
        if cached:   # switched back to a page we already read and haven't touched since: reuse it (no snapshot call)
            obs = {**cached, "from_cache": True}
            ctx.emit(f"Reused cached page snapshot for {last_url[:80]}")
        else:
            obs = c.tool(ctx, "browser_snapshot")
            if isinstance(obs, dict) and obs.get("url"):
                site_memory.cache_put(obs["url"], obs)
    else:
        sess = state.get("session") or {}
        obs = c.tool(ctx, "desktop_tree", window=sess.get("window") or ctx.spec.config.get("window", ""),
                     contains=sess.get("contains", ""))
    if isinstance(obs, dict) and isinstance(obs.get("elements"), list):
        els = obs["elements"]
        if _kind(ctx) == "desktop":   # page content first: browser frame (tabs/toolbar/bookmarks) only after it
            top = next((e["rect"][1] for e in els if e.get("control_type") == "Document" and e.get("rect")), 0)
            els = [e for e in els if (e.get("rect") or [0, 0])[1] >= top] +                   [e for e in els if (e.get("rect") or [0, 0])[1] < top]
        obs = {**obs, "elements": els[:300]}
    obs = obs if isinstance(obs, dict) else {"error": str(obs)}
    # Navigate by HTML/DOM (elements + text) — cheap and precise. Only look at a picture when the page gives us
    # almost nothing readable (canvas apps, image-heavy pages, broken snapshot).
    blank = str(obs.get("url", "")).startswith(("about:blank", "chrome://newtab", "chrome://new-tab"))
    weak = _kind(ctx) == "browser" and not blank and (bool(obs.get("error")) or (len(obs.get("elements") or []) < 3
                                                                    and len(obs.get("text") or "") < 200))
    if weak and c.can(ctx, "browser_screenshot"):
        shot = c.tool(ctx, "browser_screenshot")
        if c.ok(shot) and shot.get("png_base64"):
            obs = {**obs, "screenshot_path": shot.get("path", ""), "needs_vision": True}
            ctx.emit("Page has little readable HTML — looked at a screenshot instead")
    seen = state.get("seen", [])
    if obs.get("url") and (not seen or seen[-1].get("url") != obs["url"]):
        seen = seen + [{"url": obs["url"], "title": obs.get("title", "")}]
    # did the last action change anything? (attempted ≠ succeeded) — unchanged pages after an action are stalls
    from ..core import execstate
    sig = execstate.obs_sig(obs)
    if _kind(ctx) == "browser" and obs.get("url"):
        from .. import site_aliases
        site_aliases.learn(obs["url"])   # self-learned site names ("leetcode" → https://leetcode.com)
    hist = state.get("history") or []
    out = {"observation": obs, "visual_used": state.get("visual_used", False) or weak, "seen": seen[-40:],
           "last_obs_sig": sig}
    if hist:
        last = dict(hist[-1])
        failed = isinstance(last.get("result"), dict) and bool(last["result"].get("error"))
        # a result the tool itself VERIFIED (set_code read-back, wait_for found) succeeded even if the page text
        # looks the same — T1: re-setting already-present code was banned as "no effect" and blocked the submit path
        changed = sig != state.get("last_obs_sig") or (isinstance(last.get("result"), dict)
                                                       and bool(last["result"].get("verified")) and not failed)
        passive = last["action"].get("action") in ("wait", "scroll", "tab_switch")
        # on the real desktop, focus/keyboard/editor input is invisible in the accessibility tree: an action the
        # bridge executed OK is "done_unseen" (move on to the next step), not a failure to avoid
        unseen = (_kind(ctx) == "desktop" and not failed and not changed
                  and last["action"].get("action") in ("click", "double_click", "paste", "paste_at", "type", "hotkey", "focus")
                  and sum(1 for h in hist if execstate.action_sig(h["action"]) ==
                          execstate.action_sig(last["action"])) <= 1)
        last["status"] = "failed" if failed else ("succeeded" if changed or passive else
                                                  ("done_unseen" if unseen else "no_effect"))
        out["history"] = hist[:-1] + [last]
        if failed or (not changed and not passive and not unseen):
            a_sig = execstate.action_sig(last["action"])
            out["avoid"] = list(dict.fromkeys((state.get("avoid") or []) + [a_sig]))[-20:]
            out["stalls"] = state.get("stalls", 0) + 1
            xs = dict(state.get("xstate") or {})
            xs.setdefault("failed", []).append({"action": a_sig[:160], "url": obs.get("url", ""),
                                                "error": str((last.get("result") or {}).get("error", "no visible effect")
                                                             if isinstance(last.get("result"), dict) else "")[:160]})
            out["xstate"] = xs
        else:
            out["stalls"] = 0
    xs = dict(out.get("xstate") or state.get("xstate") or {})
    if xs:
        xs.setdefault("browser", {}).update(url=obs.get("url", ""), title=obs.get("title", ""),
                                            tabs=obs.get("tabs") or [])
        out["xstate"] = xs
    return out


_TAB_FILLER = re.compile(r"(?i)\b(i want|want|u|you|to|please|can|could|open|new|more|different|tabs?|in|my|"
                         r"chrome|browser|sandbox|the|a|an|of|with|for|on|and|"
                         r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\b")


def _plan_tab_urls(ctx, goal: str, n: int, site_urls: list[str]) -> list[str]:
    """Generic 'open 5 tabs [of youtube]' → no LLM. Specific content ('10 sorting algorithms on wikipedia') →
    ONE cheap JSON call returning N distinct URLs, then the tabs are opened deterministically."""
    rest = _TAB_FILLER.sub(" ", re.sub(r"https?://\S+", " ", goal))
    for u in site_urls:
        for part in re.findall(r"[a-z0-9]+", u.lower()):
            rest = re.sub(rf"(?i)\b{part}\b", " ", rest)
    if len(re.findall(r"[a-zA-Z]{3,}", rest)) == 0:
        return []
    d = c.ask_json(ctx, ("List exactly N distinct, real, directly-openable URLs (one per item) that satisfy the request. "
                         'Return {"urls": [..]}.'), f"N = {n}\nRequest: {goal}",
                   fallback=lambda: {"urls": []},
                   validate=lambda x: isinstance(x.get("urls"), list) and len(x["urls"]) >= 1)
    out = [u for u in d.get("urls", []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
    return list(dict.fromkeys(out))[:n]


def _rank_elements(els: list) -> list:
    """Most useful elements first, so the model sees the page's CONTENT (results, videos, articles, inputs) and not
    only the header/menus that come first in page order (YouTube: masthead + chips crowded out every video)."""
    if not els or not isinstance(els[0], dict) or "inview" not in els[0]:
        return els   # desktop trees / old snapshots: keep order

    def score(i_e):
        i, e = i_e
        label = f"{e.get('name') or ''} {e.get('text') or ''}".strip()
        if not label and e.get("role") not in ("textbox", "combobox", "searchbox"):
            return (9, i)                                   # unlabeled → last
        content_link = e.get("role") == "link" and e.get("href") and len(label) >= 12
        typing = e.get("role") in ("textbox", "combobox", "searchbox")
        rank = 0 if (content_link or typing) else 1
        return ((0 if e.get("inview") else 2) + rank, i)
    return [e for _, e in sorted(enumerate(els), key=score)]


def _compact_obs(obs: dict, budget: int = 9000) -> str:
    """Page state for the LLM: url/title/tabs + visible TEXT first (search results, titles live there), then as many
    interactive elements as fit. Previously elements came first and the text was cut off (YouTube scroll loop)."""
    head = {k: obs[k] for k in ("url", "title", "tabs", "error", "screenshot_path") if k in obs}
    text = str(obs.get("text", ""))[:4500]
    els = _rank_elements(obs.get("elements") or [])
    out = json.dumps({**head, "text": text, "elements": []}, default=str)
    for n in (80, 50, 30, 15, 0):
        cand = json.dumps({**head, "text": text, "elements": els[:n]}, default=str)
        if len(cand) <= budget:
            out = cand
            break
    return out


def decide(state: UIState, config) -> dict:
    """Every few steps the agent reports to the Main Agent first (core/supervisor.py): loops, ping-pong, wandering
    or no progress → the Main Agent redirects it (new instruction in its goal) or stops it so the plan changes."""
    from ..core import supervisor
    ctx = get_ctx(config)
    steps = state.get("steps", 0)
    extra: dict = {}
    if steps and steps % supervisor.CHECK_EVERY == 0 and state.get("sup_checked") != steps:
        v = supervisor.checkpoint(ctx, state.get("goal", ""), state.get("history", []), steps)
        extra["sup_checked"] = steps
        if v["verdict"] == "stop":
            ans = f"Stopped by the Main Agent: {v.get('note', '')} (signs: {'; '.join(v['signals'])})"
            return {**extra, "action": {"action": "done", "answer": ans}, "done": True, "answer": ans,
                    "stalled": True}
        if v["verdict"] == "redirect" and v.get("note"):
            extra["goal"] = state.get("goal", "") + f"\n(Main Agent, step {steps}: {v['note'][:300]})"
            state = {**state, "goal": extra["goal"]}
    return {**_decide(state, config), **extra}


_MEDIA_RE = re.compile(r"(?i)\b(play|listen to|watch)\b.{0,60}\b(songs?|music|tracks?|videos?)\b")
_PLAYING_JS = ("(() => { const v = document.querySelector('video'); return v ? {paused: v.paused, t: v.currentTime, "
               "ended: v.ended, ad: !!document.querySelector('.ad-showing')} : null; })()")
_WATCH_ID = re.compile(r"[?&]v=([\w-]{11})")


def _num_in(goal: str, default: int = 3) -> int:
    from .main import _NUM_WORDS
    m = re.search(r"(?i)\b(?:at least|first|play)?\s*(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s+(?:different\s+|english\s+|\w+\s+)?"
                  r"(?:songs?|tracks?|videos?)\b", goal)
    if not m:
        return default
    v = m.group(1).lower()
    return int(v) if v.isdigit() else int(_NUM_WORDS.get(v, default))


def played_videos(history: list[dict]) -> list[str]:
    """Video ids whose player was READ as really playing (not paused, time moving, not an ad) — from eval results."""
    out: list[str] = []
    for h in history:
        r = h.get("result")
        if h["action"].get("action") != "eval_readonly" or not isinstance(r, dict):
            continue
        try:
            v = json.loads(r.get("value") or "null")
        except ValueError:
            continue
        m = _WATCH_ID.search(str(r.get("url", "")))
        if m and isinstance(v, dict) and not v.get("paused") and float(v.get("t") or 0) > 1 and not v.get("ad") \
                and m.group(1) not in out:
            out.append(m.group(1))
    return out


def media_next(state: dict, obs: dict) -> dict | None:
    """'Play N songs on YouTube' as a fixed, checked sequence (owner test T4): search box → click a result → wait →
    READ the player (really playing?) → click a recommended next song → repeat. None = not a YouTube media goal."""
    goal = state.get("goal", "")
    head = goal.split("INPUTS FROM PREVIOUS STEPS")[0]
    if not (_MEDIA_RE.search(head) and re.search(r"(?i)youtube", head)):
        return None
    hist, url = state.get("history", []), str(obs.get("url", ""))
    want, played = _num_in(head), played_videos(hist)
    if len(played) >= want:
        ans = f"Played {len(played)} different songs in order (player verified playing): " + ", ".join(
            f"https://www.youtube.com/watch?v={v}" for v in played)
        return {"action": "done", "answer": ans, "verified": True}
    if len(hist) > 12 + 9 * want:
        return {"action": "done", "answer": f"Only {len(played)} of {want} songs were confirmed playing. NOT verified."}
    last = hist[-1]["action"].get("action") if hist else ""
    els = obs.get("elements") or []
    vid = (_WATCH_ID.search(url) or [None, ""])[1]

    def links(skip: set) -> list[dict]:   # real single-video links (never playlists/mixes/shorts), best visible first
        out = []
        for e in els:
            m = _WATCH_ID.search(e.get("href") or "")
            if m and "list=" not in e["href"] and m.group(1) not in skip and (e.get("text") or e.get("name")):
                out.append(e)
        return sorted(out, key=lambda e: (not e.get("inview"), e.get("y", 0)))

    if "youtube.com" not in url:
        return {"action": "goto", "url": "https://www.youtube.com", "why": "open YouTube"}
    if vid:   # on a watch page
        if vid in played:   # this one is done → click a recommended NEXT song (a real click, not a URL)
            nxt = links(set(played) | {vid})
            if nxt:
                return {"action": "click", "ref": str(nxt[0]["ref"]), "why": f"next song ({len(played)}/{want} played)"}
            return {"action": "scroll", "dy": 600, "why": "find recommended songs"}
        if last != "wait" and last != "eval_readonly":
            return {"action": "wait", "ms": 4000, "why": "let the video start"}
        tries = sum(1 for h in hist[-6:] if h["action"].get("action") == "eval_readonly")
        if last == "eval_readonly" and tries >= 3:   # not playing after 3 reads: try another song
            nxt = links({vid} | set(played))
            if nxt:
                return {"action": "click", "ref": str(nxt[0]["ref"]), "why": "this one would not play — try another"}
        if last == "eval_readonly":
            return {"action": "wait", "ms": 4000, "why": "not playing yet (maybe an ad) — wait"}
        return {"action": "eval_readonly", "expr": _PLAYING_JS, "why": "read the player: is it really playing?"}
    if "/results" in url:   # search results: click the first song that hasn't been played
        pick = links(set(played))
        if pick:
            return {"action": "click", "ref": str(pick[0]["ref"]), "why": "play a song from the results"}
        return {"action": "scroll", "dy": 600, "why": "results have not loaded / find songs"}
    box = next((e for e in els if e.get("role") in ("textbox", "combobox", "searchbox")
                and re.search(r"(?i)search", f"{e.get('name', '')} {e.get('text', '')}")), None)
    if box and last != "type":   # YouTube home: use the search box (typed + submitted)
        q = re.search(r"(?i)\b(?:songs?|music|tracks?)\b.{0,40}", head)
        genre = re.search(r"(?i)\b(english|hindi|telugu|tamil|pop|rock|classical|hip hop|k-?pop)\b", head)
        text = f"{genre.group(1) if genre else ''} songs".strip() if q else "popular songs"
        return {"action": "type", "ref": str(box["ref"]), "text": text, "submit": True, "why": "search for songs"}
    return {"action": "goto", "url": "https://www.youtube.com/results?search_query=popular+english+songs",
            "why": "search page (no search box found)"}


_BOTCHECK_RE = re.compile(r"(?i)performing security verification|verif(y|ying) you are (a )?human|are you a robot|"
                          r"complete the captcha|checking your browser before accessing|press (and|&) hold")


def _decide(state: UIState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("decide", "Deciding next action")
    obs0 = state.get("observation") or {}
    if _kind(ctx) == "browser" and _BOTCHECK_RE.search(str(obs0.get("text", ""))[:3000]):
        asked = sum(1 for h in state.get("history", []) if h["action"].get("action") == "human_check")
        if asked < 2:   # a human check (Cloudflare/captcha) is for the OWNER — ask once, wait; never fight it
            return {"action": {"action": "human_check", "url": obs0.get("url", ""),
                               "why": "the site asks for a human check"}, "done": False, "answer": ""}
        ans = (f"Blocked by a human check on {obs0.get('url', 'the site')} that wasn't completed. "
               "Tick it on the VM screen, then ask me again. NOT verified.")
        return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans, "stalled": True}
    step = media_next(state, obs0) if _kind(ctx) == "browser" and not obs0.get("error") else None
    if step:   # "play N songs on YouTube": a checked sequence, not free-form wandering (owner test T4)
        if step["action"] == "done":
            ok = bool(step.get("verified"))
            out = {"action": {"action": "done", "answer": step["answer"]}, "done": True, "answer": step["answer"]}
            return {**out, "verified": True, "evidence": step["answer"][:300]} if ok else {**out, "stalled": True}
        return {"action": step, "done": False, "answer": ""}
    kind = _kind(ctx)
    obs = state.get("observation", {})
    urls = offline_plan(state["goal"]) if kind == "browser" else []
    visited = [h["action"].get("url") for h in state.get("history", []) if h["action"].get("action") == "goto"]
    urls = [u for u in urls if u not in visited]

    if kind == "desktop" and SCREENSHOT_ONLY.search(state["goal"]) and not ACTION_WORDS.search(state["goal"]):
        # "take a screenshot of my screen": read-only — finalize captures it, no actions/approvals needed
        return {"action": {"action": "done", "answer": "Captured a screenshot of your screen."}, "done": True,
                "answer": "Captured a screenshot of your screen."}

    # "open N tabs [of X]": do it directly with real tab_new actions — no LLM call, verified by the real tab list
    want = tab_count(state["goal"]) if kind == "browser" else 0
    if want and not obs.get("error"):
        opened = sum(1 for h in state.get("history", []) if h["action"].get("action") == "tab_new")
        plan = state.get("tab_urls")
        if plan is None:
            plan = _plan_tab_urls(ctx, state["goal"], want, urls)
        if opened < want:
            site = plan[opened] if opened < len(plan) else (urls[opened % len(urls)] if urls else "about:blank")
            return {"action": {"action": "tab_new", "url": site, "why": f"open tab {opened + 1}/{want}"},
                    "done": False, "answer": "", "tab_urls": plan}
        real = obs.get("tabs") or []
        ans = (f"Opened {opened} new tab(s). Chrome now has {len(real)} tab(s) open in this window:\n"
               + "\n".join(f"{i + 1}. {u}" for i, u in enumerate(real)))
        return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans}

    # "put/paste the code into the editor": open the page if needed, then set_code with the code from the inputs —
    # deterministic, then VERIFY by reading the editor back (snapshot `editor_text` / page text contains the code)
    head = state["goal"].split("INPUTS FROM PREVIOUS STEPS")[0]
    wants_submit = kind == "browser" and bool(re.search(r"(?i)\bsubmit", head)) and bool(solution_code(state))
    if kind == "browser" and (_PASTE_RE.search(state["goal"]) or wants_submit) and not obs.get("error"):
        code = solution_code(state)
        bad = wrong_judge_format(code, state["goal"]) if code else ""
        if bad:   # never paste code the judge can't run — fail clearly so the Main Agent re-solves it (15.11)
            ans = f"Not submitted: {bad}. NOT verified."
            return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans, "stalled": True}
        hist_actions = [h["action"].get("action") for h in state.get("history", [])]
        if code and "set_code" not in hist_actions:
            target = next((u for u in c.URL_RE.findall(state["goal"].split("INPUTS FROM PREVIOUS STEPS")[0])), "")
            if target and target.rstrip("/") not in str(obs.get("url", "")).rstrip("/") \
                    and "tab_new" not in hist_actions and "goto" not in hist_actions:
                return {"action": {"action": "tab_new", "url": target.rstrip(".,;"), "why": "open the problem"},
                        "done": False, "answer": ""}
            return {"action": {"action": "set_code", "text": code, "language": code_language(code),
                               "why": "put the solution into the code editor (language picker set to match)"},
                    "done": False, "answer": ""}
        if code and "set_code" in hist_actions:
            hist = state["history"]
            i_set = max(i for i, h in enumerate(hist) if h["action"].get("action") == "set_code")
            last = hist[i_set]
            ok = isinstance(last.get("result"), dict) and last["result"].get("ok") and not last["result"].get("error")
            if not ok and isinstance(last.get("result"), dict) and last["result"].get("language"):
                ans = f"Not submitted: {last['result'].get('error', 'the editor language could not be set')}. NOT verified."
                return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans, "stalled": True}
            if ok and wants_submit:
                # SAME step, right after the verified code: Submit, then the verdict ONCE (T1: a separate submit step
                # found the editor reloaded with the site's stored C++ stub and submitted that)
                after = [h["action"] for h in hist[i_set + 1:]]
                btn = next((e for e in obs.get("elements") or [] if re.fullmatch(r"(?i)submit", (e.get("text") or "").strip())
                            or re.search(r"(?i)submit-button|^submit$", e.get("name") or "")), None)
                if not any(a.get("action") == "click" for a in after) and btn:
                    return {"action": {"action": "click", "ref": str(btn["ref"]), "why": "submit the verified code"},
                            "done": False, "answer": ""}
                if any(a.get("action") == "click" for a in after) and not any(a.get("action") == "wait_for" for a in after):
                    return {"action": {"action": "wait_for", "texts": ["Accepted", "Wrong Answer", "Time Limit Exceeded",
                                                                       "Runtime Error", "Compile Error", "Memory Limit"],
                                       "timeout_s": 40, "new_only": True, "why": "read the judge's verdict"},
                            "done": False, "answer": ""}
                # no Submit button found, or the verdict wasn't seen: the model decides below
            first_line = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")[:40]
            seen_in_page = first_line and first_line in str(obs.get("editor_text") or obs.get("text") or "")
            if ok and not wants_submit:   # paste-only steps end here; submit steps continue to the verdict
                ans = (f"Put the solution ({len(code.splitlines())} lines) into the code editor on {obs.get('url', '')}"
                       + (" — confirmed on the page." if seen_in_page else "."))
                read_back = bool(last["result"].get("verified"))
                return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans,
                        "verified": read_back or bool(seen_in_page),
                        "evidence": f"editor={last['result'].get('editor', '?')}; content read back "
                                    f"{'matches' if read_back else 'NOT confirmed'}"}

    # "close all (other) tabs (except the first/current)": deterministic, always close the LAST tab and re-observe
    m_close = re.search(r"(?i)\bclose (?:all )?(?:the )?(?:other )?tabs\b(?:.*?\bexcept\b.*?\b(first|current|this)\b)?",
                        state["goal"]) if kind == "browser" else None
    if m_close and not obs.get("error"):
        tabs = obs.get("tabs") or []
        closes = sum(1 for h in state.get("history", []) if h["action"].get("action") == "tab_close")
        if len(tabs) > 1 and closes < 40:
            keep_first = (m_close.group(1) or "first").lower() == "first"
            idx = len(tabs) - 1 if keep_first else (0 if tabs[0] != obs.get("url") else len(tabs) - 1)
            return {"action": {"action": "tab_close", "index": idx, "why": f"close tab {idx + 1}/{len(tabs)}"},
                    "done": False, "answer": ""}
        ans = f"Closed {closes} tab(s). {len(tabs)} tab(s) remain: " + ", ".join(tabs)
        return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans}

    # loop detection: same action 3x in a row, or 4 actions in a row that failed / changed nothing → stop honestly
    from ..core import execstate
    hist = state.get("history", [])
    avoid = state.get("avoid") or []
    repeated = len(hist) >= 3 and len({execstate.action_sig(h["action"]) for h in hist[-3:]}) == 1
    if repeated or state.get("stalls", 0) >= 4:
        ctx.emit("Loop detected (same action / no progress) — stopping instead of repeating", status="failed")
        text = str(obs.get("text", ""))
        tried = "; ".join(s[:80] for s in avoid[-4:])
        ans = (f"I could not finish this: my last actions on {obs.get('url', '') or 'the page'} kept failing or "
               f"changed nothing ({tried or 'repeated action'}). NOT verified as done.\n\n"
               + ("" if obs.get("error") else "Current page text:\n" + text[:1500]))
        return {"action": {"action": "done", "answer": ans}, "done": True, "answer": ans, "verified": False,
                "stalled": True}
    # avoid pointless waits: never two waits in a row
    last_act = hist[-1]["action"].get("action") if hist else ""

    def fb() -> dict:
        if obs.get("error"):
            return {"action": "done", "answer": f"Cannot observe the {kind}: {obs['error']}"}
        # only URLs the goal really contains — guessed sites ("open in…" → www.in.com) only in offline/mock mode
        real = [u for u in urls if u in state["goal"] or ctx.llm().mode == "mock"]
        if kind == "browser" and real:
            return {"action": "goto", "url": real[0], "why": "open the requested page"}
        return {"action": "done", "answer": f"Page: {obs.get('title', '')} {obs.get('url', '')}\n\n"
                                            f"{str(obs.get('text', ''))[:1500]}"}

    actions = ("goto{url}, click{ref}, type{ref,text,submit}, press{key}, select{ref,value}, scroll{dy}, back, wait{ms}, "
               "tab_new{url} (the ONLY way to open a tab), tab_switch{index}, tab_close{index}, "
               "set_code{text} (put code into a code editor such as LeetCode's), phone{text,screenshot} (send the "
               "owner a progress update on their phone — only for real milestones). SEARCHING A SITE: go straight "
               "to its search URL (e.g. https://www.youtube.com/results?search_query=<words>) or type{ref,text,"
               "submit:true} into the search box — never click a search box again and again (clicking an input "
               "changes nothing; TYPE into it). PLAYING a video/song: open a result (its /watch page) and verify the "
               "URL is a /watch page and the player is playing. ONLINE CODE JUDGES (LeetCode, Codeforces, …): put the "
               "code in with set_code{text} (verified read-back); then click the button whose name/text is Submit "
               "(e.g. 'console-submit-button') — NEVER Run, Debug or bug icons, they don't submit; then wait_for{texts:"
               "['Accepted','Wrong Answer','Time Limit Exceeded','Runtime Error','Compilation Error','Memory Limit'], "
               "timeout_s:40, new_only:true} ONCE (new_only: the page's own statistics also say 'Accepted'). Menus/drawers not listed in the observation are closed — don't try to close them."
               if kind == "browser" else "phone{text,screenshot} (progress update to the owner's phone), " "click{ref} or click{x,y} (screen coordinates), double_click{ref|x,y}, "
               "type{ref?,text}, paste{text} (text may be $CODE = the code block from INPUTS, pasted exactly), "
               "paste_at{x,y,text} (click a point, select all, paste — ONE step; best for code editors), "
               "open_url{url} (go to a page in the open Chrome window — NEVER type URLs yourself; links in the "
               "observation carry their target in `value`, open those with open_url), "
               "locate{what} (LOOK at a screenshot to get x,y of something NOT in the tree, e.g. 'the code editor "
               "text area'), "
               "hotkey{keys e.g. ctrl+a}, focus{ref='w0'}, wait_for{texts:[...], timeout_s} (observe until one of the "
               "texts is visible — use it to VERIFY results such as 'Accepted'). Open Chrome pages only with the "
               "already-open window (never launch Chrome). CODE EDITORS on web pages (Monaco/CodeMirror) usually have "
               "NO element: click{x,y} at a point INSIDE the editor's text area (below its header/tab bar, using the "
               "rects of nearby elements such as the editor panel's tabs/language button, or locate{what:'code editor text "
               "area'} to get them from a screenshot) — then paste_at{x,y,text:'$CODE'} which clicks there, selects "
               "all and pastes in one step. Missing from the tree ≠ missing from the screen: use locate, don't refocus. Don't 'focus' twice in a row — focusing does not put text anywhere.")
    xs = state.get("xstate") or {}
    hist_view = [{"action": h["action"], "status": h.get("status", "attempted"),
                  "result": h.get("result")} for h in state.get("history", [])[-8:]]
    sys_prompt = (
        f"You control a {kind} to accomplish a goal. Loop: OBSERVE → ACT → OBSERVE → VERIFY. Choose ONE next action "
        f"from: {actions}, or done{{answer}} ONLY when the observation PROVES the goal is achieved (e.g. after "
        "clicking Submit, the result page must show the outcome — clicking is not success). Use element refs from the "
        "observation. Keyboard shortcuts (Ctrl+T, Ctrl+Tab...) do NOT work — use the tab_* actions. `tabs` in the "
        "observation is the real list of open tabs. History statuses: succeeded = page changed, no_effect = nothing "
        "changed, failed = error, done_unseen = it ran but its effect (focus, typed/pasted text) can't be seen in "
        "the tree — treat it as done and do the NEXT step of your plan (e.g. after clicking into an editor: ctrl+a, "
        "then paste), never repeat it. NEVER repeat an action listed under AVOID — choose a DIFFERENT strategy (another "
        "element, a direct URL, search, a different page). Do not wait unless a page is visibly loading. Return "
        "{\"action\": ..., <params>, \"why\": str}.")
    user_prompt = (f"Goal: {state['goal']}\n{execstate.handoff(xs, limit=900) if xs else ''}\n"
                   f"{_site_hint(kind, obs, state['goal'])}"
                   f"AVOID (failed or had no effect): {json.dumps(avoid[-8:])[:900]}\n"
                   f"History: {json.dumps(hist_view, default=str)[:3500]}\n"
                   f"Observation: {_compact_obs(obs)}")
    rejected: list[str] = []

    def valid(d: dict) -> bool:
        why = ("" if d.get("action") else "no action") or (
            "it is on the AVOID list (it failed or changed nothing before)" if d.get("action") != "done"
            and execstate.action_sig(dict(d)) in avoid else "") or (
            "two waits in a row" if d.get("action") == "wait" and last_act == "wait" else "")
        if why:
            rejected.append(f"{json.dumps({k: v for k, v in d.items() if k != 'why'}, default=str)[:200]} — {why}")
        return not why

    role = "vision" if kind == "desktop" else None
    out = c.ask_json(ctx, sys_prompt, user_prompt, fallback=lambda: {"_rejected": True}, validate=valid, role=role)
    if out.get("_rejected") and rejected:
        # the model DID answer but the action was rejected: tell it why and ask ONCE more (T1: 19 rejections fell
        # through to the URL guesser, which then visited invented sites)
        out = c.ask_json(ctx, sys_prompt, user_prompt + "\n\nYOUR LAST PROPOSAL WAS REJECTED: " + rejected[-1]
                         + ". Propose a DIFFERENT action, or done{answer:'NOT verified: <why>'} if nothing else can "
                           "work.", fallback=fb, validate=valid, role=role)
    elif out.get("_rejected"):
        out = fb()
    if out.get("action") != "done" and execstate.action_sig(out) in avoid:   # fallback picked a banned action
        out = {"action": "done", "answer": "I could not find a different way to proceed. NOT verified as done."}
    return {"action": out, "done": out.get("action") == "done", "answer": out.get("answer", "")}


def verify(state: UIState, config) -> dict:
    """VERIFY: before claiming success, check the CURRENT real page against the goal (not the agent's own claim)."""
    ctx = get_ctx(config)
    if state.get("stalled") or state.get("blocked"):
        return {"verified": False, "evidence": "stopped before finishing"}
    if state.get("verified") and state.get("evidence"):   # already verified deterministically (e.g. editor read-back)
        return {}
    last = (state.get("history") or [{}])[-1]
    lr = last.get("result") if isinstance(last.get("result"), dict) else {}
    if last.get("action", {}).get("action") == "wait_for" and lr.get("found"):
        ok = lr.get("verified", False) and not re.search(r"(?i)wrong|error|exceeded|failed", lr["found"])
        return {"verified": ok, "evidence": f"seen on screen: '{lr['found']}' {lr.get('details', '')[:200]}"}
    obs = state.get("observation") or {}
    if obs.get("error"):
        return {"verified": False, "evidence": f"cannot observe: {obs['error']}"}
    level = c.level(ctx)
    goal = state.get("goal", "")
    # deterministic goals verified by the real tab list / URL; cheap tiers trust a clean observation
    if tab_count(goal) and obs.get("tabs") is not None:
        # only tabs that really loaded a page count (16.15: a chrome-error:// tab and about:blank were counted)
        good = [t for t in obs["tabs"] if re.match(r"(?i)https?://|file://", t)]
        bad = [t for t in obs["tabs"] if t.startswith("chrome-error://")]
        ok = len(good) >= tab_count(goal) and not bad
        out = {"verified": ok, "evidence": f"{len(good)} loaded tab(s) of {tab_count(goal)} wanted: "
                                           + ", ".join(good[:12]) + (f" · failed to load: {len(bad)}" if bad else "")}
        tries = state.get("verify_tries", 0) + 1
        if not ok and tries < 2 and state.get("steps", 0) < state.get("max_steps", MAX_STEPS):
            out.update(done=False, answer="", verify_tries=tries,
                       goal=goal + f"\n(Verifier: only {len(good)} tab(s) loaded — close the failed tab(s) "
                                   f"(tab_close) and open other working pages until {tab_count(goal)} have loaded.)")
        return out
    if level <= 1 and not re.search(r"(?i)\b(submit|send|post|buy|book|order|save|create|upload|delete)\b", goal):
        return {"verified": True, "evidence": f"page: {obs.get('title', '')} {obs.get('url', '')}"}
    ctx.node("verify", "Verifying the result on the real page")
    d = c.ask_json(ctx, (
        "You are a strict verifier. Using ONLY the current page observation, decide whether the goal is actually "
        "achieved. Actions being attempted is not proof; the page must show the result (e.g. 'Accepted', the item in "
        "the cart, the message in the thread, the opened page). Return {\"verified\": bool, \"evidence\": "
        "\"quote/describe what on the page proves it, or what is missing\", \"next\": \"if not verified: the next "
        "different action to try, else ''\"}."),
        f"Goal: {goal}\nAgent claims: {str(state.get('answer', ''))[:600]}\nObservation: {_compact_obs(obs, 6000)}",
        fallback=lambda: {"verified": False, "evidence": "verification unavailable (no model)", "next": ""},
        validate=lambda x: "verified" in x)
    if not d["verified"] and _kind(ctx) == "desktop" and c.can(ctx, "desktop_screenshot"):
        # the accessibility tree can't show everything (editor text, canvas, focus): LOOK at the real screen
        shot = c.tool(ctx, "desktop_screenshot")
        b64 = shot.get("png_base64") if isinstance(shot, dict) else ""
        if b64:
            v = ctx.llm().vision_json(
                "You are a strict verifier looking at a real screenshot of the user's screen. Decide whether the goal "
                "is actually achieved on screen right now. Only what is visibly shown counts. Return {\"verified\": "
                "bool, \"evidence\": \"what on screen proves it, or what is missing\", \"next\": \"if not "
                "verified: the next different action to try, else ''\"}.",
                f"Goal: {goal}\nAgent claims: {str(state.get('answer', ''))[:400]}",
                b64, fallback=lambda: {})
            if "verified" in v:
                d = {**v, "evidence": "(screenshot) " + str(v.get("evidence", ""))}
    tries = state.get("verify_tries", 0) + 1
    ctx.emit(("Verified: " if d["verified"] else "NOT verified: ") + str(d.get("evidence", ""))[:200],
             status="done" if d["verified"] else "failed")
    out = {"verified": bool(d["verified"]), "evidence": str(d.get("evidence", ""))[:400], "verify_tries": tries}
    if not d["verified"] and tries < 2 and state.get("steps", 0) < state.get("max_steps", MAX_STEPS) and d.get("next"):
        out.update(done=False, answer="", goal=goal + f"\n(Verifier: not done yet — {d.get('evidence', '')[:200]}. "
                                                       f"Suggested next: {d['next'][:200]})")
    return out


def after_verify(state: UIState) -> str:
    return "observe" if not state.get("done") else "finalize"


def _site_hint(kind: str, obs: dict, goal: str) -> str:
    if kind != "browser" or not obs.get("url"):
        return ""
    from .. import site_memory
    h = site_memory.hint(obs["url"], goal)
    return f"Site memory: {h}\n" if h else ""


def gate(state: UIState, config) -> dict:
    """Sensitive-action gate: purchases, sending, submitting personal data... need explicit approval.
    Desktop actions always require approval (they touch the real machine)."""
    ctx = get_ctx(config)
    a = state["action"]
    target = ""
    for el in state.get("observation", {}).get("elements", []) or []:
        if str(el.get("ref")) == str(a.get("ref")):
            target = f"{el.get('role', el.get('control_type', ''))} '{el.get('name', el.get('text', ''))}'"
            break
    desc = f"{a.get('action')} {target} {a.get('text', '') if a.get('action') == 'type' else ''} {a.get('url', '')}".strip()
    # desktop_act asks for approval itself (per action), so only browser actions are gated here
    risky = _kind(ctx) == "browser" and (bool(SENSITIVE.search(desc)) or (a.get("action") == "type" and a.get("submit")))
    if not risky:
        return {"blocked": ""}
    ctx.node("gate", f"Approval needed: {desc[:80]}")
    try:
        ctx.approve(f"{ctx.spec.name} wants to: {desc[:150]}", {"action": a, "goal": state["goal"],
                                                                 "url": state.get("observation", {}).get("url")})
        return {"blocked": ""}
    except ApprovalDenied:
        return {"blocked": desc, "done": True, "answer": f"Stopped: you denied '{desc}'."}


def act(state: UIState, config) -> dict:
    ctx = get_ctx(config)
    a = dict(state["action"])
    name = a.pop("action")
    why = a.pop("why", None)
    ctx.node("act", f"{name} {json.dumps(a)[:80]}", why=str(why or "")[:200])
    if _kind(ctx) == "browser" and a.get("ref") and not str(a["ref"]).isdigit():
        # the model sometimes passes an element's NAME as its ref ("Submit", "console-submit-button")
        want = str(a["ref"]).strip().lower()
        el = next((e for e in (state.get("observation") or {}).get("elements") or []
                   if want in ((e.get("name") or "").lower(), (e.get("text") or "").strip().lower())), None)
        if el:
            a["ref"] = str(el["ref"])
    if _kind(ctx) == "desktop" and isinstance(a.get("text"), str) and "$CODE" in a["text"]:
        a["text"] = a["text"].replace("$CODE", solution_code(state) or "")   # exact code, no retyping
    if name == "set_code" and _kind(ctx) == "browser":
        exact = solution_code(state)   # the solver's own code beats the model's retyped (or remembered) copy
        if exact and not wrong_judge_format(exact, state.get("goal", "")):
            a["text"] = exact
        if not a.get("language") and code_language(a.get("text", "")):
            a["language"] = code_language(a["text"])   # T1: Python submitted while the picker said C++
    if name == "human_check":   # ask the owner once, then watch the page (≤3 min) until the check is gone
        from .. import notify, vmbrowser
        site = (re.search(r"https?://([^/]+)", a.get("url", "")) or [None, "the site"])[1]
        msg = (f"A human check (captcha) is needed on {site}. Open {vmbrowser.view_url()} and tick the box — "
               "I'll continue by myself.")
        ctx.emit(msg, status="waiting", human_check=True)
        if ctx.run.notify:
            notify.send("AgentOS needs you for a moment", msg, event="waiting")
        r = {"cleared": False}
        for _ in range(36):
            time.sleep(5)
            snap = c.tool(ctx, "browser_snapshot")
            if isinstance(snap, dict) and not snap.get("error") and not _BOTCHECK_RE.search(str(snap.get("text", ""))[:3000]):
                r = {"ok": True, "cleared": True, "url": snap.get("url", "")}
                break
        ctx.emit("Human check passed — continuing" if r.get("cleared") else "Human check still there after 3 min",
                 status="done" if r.get("cleared") else "failed")
    elif name == "phone":   # progress update (optionally with a screenshot) to the owner's phone
        r = c.tool(ctx, "phone_update", text=str(a.get("text") or "")[:1500], screenshot=bool(a.get("screenshot")))
    elif _kind(ctx) == "desktop" and name == "locate":
        sess = state.get("session") or {}
        r = c.tool(ctx, "desktop_locate", what=str(a.get("what") or a.get("text") or "the target"),
                   window=" ".join(x for x in (sess.get("window", ""), sess.get("contains", "")) if x))
    elif _kind(ctx) == "desktop" and name == "wait_for":
        sess = state.get("session") or {}
        r = c.tool(ctx, "desktop_wait_for", texts=a.get("texts") or [a.get("text", "")],
                   window=sess.get("window", ""), contains=sess.get("contains", ""),
                   timeout_s=int(a.get("timeout_s") or 90))
    else:
        r = c.tool(ctx, "browser_act" if _kind(ctx) == "browser" else "desktop_act", action=name, **a)
    rec = {"action": name, **a}
    if a.get("ref"):   # remember WHICH element (role + name) worked, for site memory
        for el in state.get("observation", {}).get("elements", []) or []:
            if str(el.get("ref")) == str(a["ref"]):
                rec.update(ref_name=str(el.get("name") or el.get("text") or "")[:60], role=el.get("role", ""))
                break
    if isinstance(r, dict):   # keep the structured result (ok/verified/editor) — only shorten long fields
        res = {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in r.items()
               if k not in ("png_base64", "editor_text", "elements")}
    else:
        res = c.short(r, 300)
    # status starts as "attempted"; observe() upgrades it to succeeded / no_effect / failed from the NEW page
    hist = state.get("history", []) + [{"action": rec, "result": res, "status": "attempted"}]
    out = {"history": hist, "steps": state.get("steps", 0) + 1}
    if name == "wait_for" and isinstance(r, dict) and r.get("found") and r.get("verified") \
            and not re.search(r"(?i)wrong|error|exceeded|failed", str(r["found"])):
        # the verdict the agent was waiting for is ON the screen: finish now (no second look, no repeated wait_for)
        hist[-1]["status"] = "succeeded"
        out.update(done=True, answer=f"Seen on screen: '{r['found']}'. {str(r.get('details', ''))[:300]}")
    return out


def after_act(state: UIState) -> str:
    return "verify" if state.get("done") else "observe"


def finalize(state: UIState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Report")
    arts = []
    shot_tool = "browser_screenshot" if _kind(ctx) == "browser" else "desktop_screenshot"
    wants_picture = _kind(ctx) == "desktop" or state.get("visual_used") or re.search(
        r"(?i)screen ?shot|picture|image|photo|snapshot|show me|capture|how (it|the page) looks", state.get("goal", ""))
    if c.can(ctx, shot_tool) and not state.get("blocked") and wants_picture:
        shot = c.tool(ctx, shot_tool)
        if c.ok(shot) and shot.get("png_base64"):
            title = "Final screenshot" if _kind(ctx) == "browser" else "Screen screenshot"
            if shot.get("path"):
                title += f" (saved to {shot['path']})"
            arts.append({"kind": "image", "title": title, "path": shot.get("path", ""),
                         "content": "data:image/png;base64," + shot["png_base64"]})
        elif isinstance(shot, dict) and shot.get("error") and _kind(ctx) == "desktop":
            ctx.emit(f"Screenshot failed: {shot['error']}", status="failed")
    verified = bool(state.get("verified"))
    status = "success" if verified else "partial"     # success ONLY when the result was verified on the real page
    if state.get("observation", {}).get("error") and not state.get("history"):
        status = "failed"
    sess_err = (state.get("session") or {}).get("error")
    if sess_err:
        status = "failed"
        state = {**state, "answer": f"Could not open the browser with profile '{state.get('profile') or '?'}': "
                                    f"{sess_err}. Nothing was done in the browser."}
    if not verified and status != "failed" and state.get("answer") and "NOT verified" not in state["answer"]:
        state = {**state, "answer": state["answer"] + "\n\n⚠️ Not verified: " + (state.get("evidence")
                                                                                   or "the page did not confirm it")}
    # learning: remember what failed (avoid) and what worked, scoped to the site
    from ..core import execstate
    from .. import site_memory as _sm
    dom = _sm.domain_of((state.get("observation") or {}).get("url", "")) or ctx.spec.id
    for sig_ in (state.get("avoid") or [])[-3:]:
        execstate.record_lesson(dom, "avoid", f"{sig_[:140]} had no effect / failed",
                                why=str(state.get("evidence", ""))[:120])
    if verified and state.get("history"):
        path = " → ".join(h["action"].get("action", "") + (f"({h['action'].get('ref_name', '')})"
                                                           if h["action"].get("ref_name") else "")
                          for h in state["history"] if h.get("status") == "succeeded")[:200]
        if path:
            execstate.record_lesson(dom, "worked", f"goal '{state.get('goal', '')[:60]}': {path}")
    xs = dict(state.get("xstate") or {})
    if xs:
        step_name = ctx.task.task_id
        (xs.setdefault("completed", []) if verified else xs.setdefault("failed", [])).append(
            {"step": step_name, "summary": (state.get("answer") or "")[:160], "verified": verified,
             "evidence": str(state.get("evidence", ""))[:160]})
        arts.append({"kind": "state", "title": "task state", "content": xs})
    if _kind(ctx) == "browser" and state.get("history"):
        try:
            from .. import site_memory
            site_memory.record_run(state.get("goal", ""), state["history"], state.get("seen", []),
                                   success=status == "success")
        except Exception as e:  # noqa: BLE001 - learning must never break a run
            ctx.emit(f"site memory not updated: {e}")
    steps = "\n".join(f"{i + 1}. {h['action']}" for i, h in enumerate(state.get("history", [])))
    shot_note = ""
    if arts:
        shot_note = (f"\n\n📸 Screenshot saved to `{arts[0]['path']}` and attached to this report." if arts[0].get("path")
                     else f"\n\n📸 {arts[0]['title']} attached to this report (not saved to a file).")
    return {"report": ctx.report((state.get("answer") or "Finished").split("\n")[0][:300],
                                 result=(state.get("answer", "") + shot_note +
                                         ("\n\n**Steps:**\n" + steps if steps else "")).strip(),
                                 status=status, artifacts=arts,
                                 errors=[f"blocked: {state['blocked']}"] if state.get("blocked") else [])}


def after_decide(state: UIState) -> str:
    if state.get("done"):
        return "verify"
    if state.get("steps", 0) >= state.get("max_steps", MAX_STEPS):
        return "finalize"
    return "gate"


def after_gate(state: UIState) -> str:
    return "finalize" if state.get("blocked") else "act"


def _build(spec):
    g = StateGraph(UIState)
    for n, fn in [("start", start), ("observe", observe), ("decide", decide), ("gate", gate), ("act", act),
                  ("verify", verify), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "start")
    # no session in the requested profile → stop and report (never retry blindly or fall back to another profile)
    g.add_conditional_edges("start", lambda s: "finalize" if (s.get("session") or {}).get("error") else "observe",
                            ["observe", "finalize"])
    g.add_edge("observe", "decide")
    g.add_conditional_edges("decide", after_decide, ["gate", "verify", "finalize"])
    g.add_conditional_edges("verify", after_verify, ["observe", "finalize"])
    g.add_conditional_edges("gate", after_gate, ["act", "finalize"])
    g.add_conditional_edges("act", after_act, ["observe", "verify"])
    g.add_edge("finalize", END)
    return g


@archetype("browser", "Web operator: open → observe (DOM snapshot) → decide → sensitive-action gate → act loop")
def build_browser(spec):
    return _build(spec)


@archetype("desktop", "Desktop operator via Host Bridge: observe UIA tree → decide → approve every action → act loop")
def build_desktop(spec):
    return _build(spec)
