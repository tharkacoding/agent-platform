"""Browser tools — Playwright in the sandboxed `browser` container, or the user's own Chrome via the host bridge (CDP)."""
import base64
import re

from .. import config, settings
from ..clients import ServiceUnavailable, bridge, browser
from ..core.sdk import tool

_SESSIONS: dict[str, dict] = {}   # "<run_id>:<task_id>" -> {"session_id", "mode", "profile", "svc"} — one per STEP
_RUN_SIDS: dict[str, list[dict]] = {}   # run_id -> every session it opened (closed together when the run ends)


def _key(ctx) -> str:
    """One browser session (= its own tab) per plan step: parallel steps never drive the same tab (T1: two browser
    steps fought over one LeetCode tab). Sequential steps continue on the tab the previous step released."""
    return f"{ctx.run.run_id}:{getattr(ctx.task, 'task_id', '') or ''}"


def _put(ctx, sess: dict) -> None:
    _SESSIONS[_key(ctx)] = sess
    _RUN_SIDS.setdefault(ctx.run.run_id, []).append(sess)


def release_step(run_id: str, task_id: str) -> None:
    """A step finished: give its tab back (kept open, logged in) so the next step can continue on it."""
    sess = _SESSIONS.pop(f"{run_id}:{task_id}", None)
    if sess and sess.get("mode") != "extension":
        try:
            _svc(sess, "POST", "/session/release", json={"session_id": sess["session_id"]}, timeout=10)
        except Exception:  # noqa: BLE001 - cleanup must never break a run
            pass


def _svc(s: dict, method: str, path: str, **kw):
    """The browser service that owns this session: the local one, or the test VM's (svc='vm', see aap/vmbrowser)."""
    if s.get("svc") == "vm":
        from .. import vmbrowser
        return vmbrowser.call(method, path, **kw)
    return browser(method, path, **kw)


def close_run(run_id: str) -> None:
    """Run finished: close all its browser sessions (sandbox tabs closed; tabs in the user's Chrome stay open)."""
    for k in [k for k in _SESSIONS if k.split(":", 1)[0] == run_id]:
        _SESSIONS.pop(k, None)
    for sess in _RUN_SIDS.pop(run_id, []):
        if sess.get("mode") == "extension":
            continue
        try:
            _svc(sess, "POST", "/session/close", json={"session_id": sess["session_id"]}, timeout=20)
        except Exception:  # noqa: BLE001 - cleanup must never break a run
            pass
SENSITIVE = re.compile(r"(?i)\b(buy|purchase|pay|checkout|order|send|post|publish|submit|delete|transfer|"
                       r"password|confirm|subscribe|place order)\b")


def resolve_mode(requested: str | None, agent_browser_perm: str) -> str:
    """'sandbox' | 'host'. Empty request → Settings 'browser_default_mode'. Host is only possible for agents whose
    manifest allows browser='host'; everyone else silently gets the sandbox."""
    mode = (requested or settings.get("browser_default_mode") or "sandbox").lower()
    if mode not in ("sandbox", "host", "extension", "vm"):
        mode = "sandbox"
    if mode == "host" and settings.get("browser_default_mode") == "extension":
        mode = "extension"    # "my chrome" with the extension chosen in Settings → drive the real Chrome via it
    if mode == "extension":
        from ..extension import hub
        if not hub.connected and requested != "extension":
            mode = "host"     # extension chosen but not connected → fall back to the AgentOS Chrome profile
    if mode == "extension":   # the extension drives the user's real Chrome → same permission as host mode
        return "extension" if agent_browser_perm == "host" else "sandbox"
    return "host" if mode == "host" and agent_browser_perm == "host" else "sandbox"


def _session(ctx) -> dict:
    s = _SESSIONS.get(_key(ctx))
    if s:
        return s
    if "browser_open" in ctx.spec.tools:   # through call_tool → permission check + host-mode approval (once per run)
        return ctx.call_tool("browser_open", mode=resolve_mode("", ctx.spec.permissions.browser))
    return browser_open(ctx, mode="sandbox")


def _open_risk(args: dict):
    # approval is asked when this call will attach to the user's real Chrome; the agent's permission is applied
    # again inside browser_open, so a sandbox-only agent never reaches host mode
    mode = (args.get("mode") or settings.get("browser_default_mode") or "sandbox").lower()
    if mode == "extension":
        return "control your real Chrome tabs through the AgentOS extension"
    return "attach to your real Chrome profile" if mode == "host" else None


def _risk_act(args: dict):
    label = " ".join(str(v) for k, v in args.items() if k in ("selector", "text", "name", "label", "ref_name"))
    if args.get("action") in ("click", "press", "type") and SENSITIVE.search(label):
        return f"sensitive browser action ({args.get('action')} '{label[:80]}')"
    return None


# ---------------------------------------------------------------------------------------------- extension mode
_WRITE_ACTIONS = {"click", "type", "press", "select", "set_code"}


def _ext_call(s: dict, cmd: str, args: dict) -> dict:
    from ..extension import ExtensionUnavailable, hub
    try:
        r = hub.call(cmd, args, profile=s.get("ext_profile") or None)   # None = the single/active extension
    except ExtensionUnavailable as e:
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - extension-side error / timeout
        return {"error": f"extension: {e}"}
    return r if isinstance(r, dict) else {"result": r}


def _ext_open(ctx, profile: str = "") -> dict:
    """No profile: the tab the user picked ('use this tab' on the Browser page), else the focused tab.
    With a profile (routed by browser_open): that profile's extension; the picked tab if it is in that profile,
    else a FRESH tab — never the tab the owner is reading."""
    from ..extension import hub, norm
    if not (hub.conn(profile) if profile else hub.connected):
        return {"error": "Chrome extension not connected — load extension/ in chrome://extensions and pair it"}
    probe = {"ext_profile": profile}
    tabs = _ext_call(probe, "tabs.list", {})
    if tabs.get("error"):
        return tabs
    lst = tabs.get("tabs") or []
    mine = not profile or not hub.active_profile or norm(hub.active_profile) == norm(profile)
    tab = (next((t for t in lst if t.get("id") == hub.active_tab), None) if mine else None)
    if not tab and not profile:
        tab = next((t for t in lst if t.get("active")), None)
    if not tab:
        tab = _ext_call(probe, "tabs.open", {"url": "about:blank"})
        if tab.get("error"):
            return tab
    s = {"session_id": "ext", "mode": "extension", "tab_id": tab["id"], "profile": profile or "your Chrome",
         "ext_profile": profile}
    _put(ctx, s)
    _ext_call(s, "banner", {"tab_id": tab["id"], "on": True})
    ctx.emit(f"Using your Chrome tab via the extension: {tab.get('title', '')[:60]}", status="info", host_browser=True)
    return {**s, "url": tab.get("url", ""), "title": tab.get("title", "")}


def _ext_act(ctx, s: dict, action: str, kw: dict) -> dict:
    from .. import extension
    args = {k: v for k, v in kw.items() if v not in ("", None) and k != "ref_name"}
    if action == "tab_new":
        r = _ext_call(s, "tabs.open", {"url": args.get("url") or "about:blank"})
        if r.get("id"):
            s["tab_id"] = r["id"]
            _ext_call(s, "banner", {"tab_id": r["id"], "on": True})
            return {"ok": True, "url": r.get("url", ""), "title": r.get("title", ""), "tab_id": r["id"]}
        return r
    if action in ("tab_switch", "tab_close"):
        lst = (_ext_call(s, "tabs.list", {}).get("tabs") or [])
        idx = int(args.get("index", 0) or 0)
        if not 0 <= idx < len(lst):
            return {"error": f"index must be 0..{len(lst) - 1}"}
        tid = lst[idx]["id"]
        if action == "tab_switch":
            r = _ext_call(s, "tabs.activate", {"tab_id": tid})
            s["tab_id"] = tid
            return {"ok": True, "url": lst[idx].get("url", ""), "title": lst[idx].get("title", "")} if not r.get("error") else r
        r = _ext_call(s, "tabs.close", {"tab_id": tid})
        if tid == s.get("tab_id"):
            rest = [t for t in lst if t["id"] != tid]
            s["tab_id"] = rest[-1]["id"] if rest else None
        return r
    if action in _WRITE_ACTIONS:
        c = extension.hub.conn(s.get("ext_profile") or None)
        url = next((t.get("url", "") for t in (c.tabs if c else []) if t.get("id") == s.get("tab_id")), "")
        if not extension.site_allowed(url, s.get("ext_profile") or None):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or "this site"
            ctx.approve(f"extension: act on {host}", {"action": action, "url": url,
                                                      "hint": "add the site to Settings → extension allowlist to stop asking"})
            args["approved"] = True   # extension enforces read-only on non-allowlisted sites without this flag
    return _ext_call(s, "act", {"tab_id": s.get("tab_id"), "action": action, **args})


def resolve_profile(ctx, profile: str = "") -> tuple[str, str]:
    """(profile, source). Order: explicit arg → task state (user said it) → the request text → saved preference.
    Never returns '' — and never silently swaps a user-specified profile for another one."""
    from ..core import execstate
    if profile:
        return profile, "argument"
    st = execstate.from_context(ctx.task.context)
    if st and (st.get("browser") or {}).get("profile"):
        return st["browser"]["profile"], "task state"
    p = execstate.parse_profile(ctx.task.instruction)
    # the user's own words (agent run directly) are trusted; planner-written instructions only for REAL profiles
    if p and (not ctx.task.parent_agent or _known_profile(p)):
        return p, "request"
    low = (ctx.task.instruction or "").lower()
    for site, prof in (settings.get("site_profiles") or {}).items():   # a profile the owner chose FOR this site
        if site and re.search(rf"\b{re.escape(site)}\b", low):
            return prof, "preference"
    return settings.get("browser_profile") or "default", "preference"


_KNOWN: dict = {"ts": 0.0, "names": set(), "real": set(), "agentos": set()}


def _is_real_profile(name: str) -> bool:
    n = name.strip().lower()
    if n == "default":   # AgentOS's own browser profile is called "default"; Chrome's folder "Default" is the owner's
        return False     # → the owner's main profile is reached by its display name ("Your Chrome") or the bridge
    from .. import vmbrowser
    if vmbrowser.has(n):  # the owner logged in to this profile in the test VM: browser_agent works there, no screen
        return False
    _known_profile(name)          # refreshes the cache
    return n in _KNOWN["real"]


def _imported(name: str) -> bool:
    """AgentOS copy that actually holds imported logins (import writes an 'imported_from' marker)."""
    from pathlib import Path
    import os
    d = Path(os.environ.get("APPDATA", "")) / "AgentOS" / "chrome-profiles" / name
    return (d / "imported_from.json").exists() or (d / ".imported").exists()


def _known_profile(name: str) -> bool:
    """Real Chrome profiles (display name or dir), AgentOS profiles, or the saved preference."""
    import time as _t
    n = name.strip().lower()
    if n == (settings.get("browser_profile") or "").lower():
        return True
    if _t.time() - _KNOWN["ts"] > 60:
        names: set[str] = set()
        try:
            r = bridge("GET", "/chrome/profiles", timeout=10)
            if isinstance(r, dict):
                real = set()
                for pr in r.get("profiles") or []:
                    real.update({(pr.get("name") or "").lower(), (pr.get("dir") or "").lower()})
                names.update(real)
                names.update(x.lower() for x in r.get("agentos_profiles") or [])
                _KNOWN["real"] = real - {""}
        except Exception:  # noqa: BLE001
            pass
        from .. import vmbrowser
        names.update(vmbrowser.profiles())
        _KNOWN.update(ts=_t.time(), names=names - {""})
    return n in _KNOWN["names"]


@tool("browser_open","Open a browser session. mode=sandbox (isolated Chromium) or host (your Chrome via the host "
      "bridge; needs approval). Empty mode = the default chosen in Settings → Browser. profile = the Chrome "
      "profile to use (keep the one the user asked for).",
      {"profile": {"type": "string", "default": ""}, "mode": {"type": "string", "default": ""}},
      needs="browser", risky=_open_risk)
def browser_open(ctx, profile: str = "", mode: str = ""):
    profile, source = resolve_profile(ctx, profile)
    existing = _SESSIONS.get(_key(ctx))
    if existing and existing.get("profile") == profile:
        return {"session_id": existing["session_id"], "mode": existing["mode"], "profile": profile, "reused": True}
    if existing:
        ctx.emit(f"Switching browser profile {existing.get('profile')} → {profile}")
        _SESSIONS.pop(_key(ctx), None)
    mode = resolve_mode(mode, ctx.spec.permissions.browser)
    from .. import extension, vmbrowser
    if ctx.spec.permissions.browser == "host" and extension.ready(profile):
        # the owner switched this profile ON for AgentOS and its extension is connected: full access to that
        # profile's own tabs/logins (before the VM copy or the screen-driven real profile), approval as for any host
        ctx.approve(f"control your Chrome profile '{profile}' through the AgentOS extension",
                    {"profile": profile, "via": "extension"})
        return _ext_open(ctx, profile)
    if vmbrowser.url() and (mode == "vm" or vmbrowser.has(profile)):
        return _vm_open(ctx, profile)
    if mode == "vm":
        mode = "sandbox"   # VM chosen but not configured → local sandbox
    if mode == "host" and source in ("request", "task state", "argument") and _is_real_profile(profile) \
            and not _imported(profile):
        # never silently use AgentOS's EMPTY copy when the user meant their real, logged-in profile
        return {"error": f"'{profile}' is your real Chrome profile (with your logins). The browser agent can't "
                         f"automate real profiles — use the Computer Agent (desktop_agent: chrome_open_profile + "
                         f"desktop_tree/desktop_act), or import the profile first (Browser page).",
                "use_agent": "desktop_agent", "profile": profile}
    from ..core import execstate as _xs
    if source == "request" and not ctx.task.parent_agent and _xs.is_standing(ctx.task.instruction):
        site = _xs.standing_site(ctx.task.instruction)
        if site:   # "always use asdf FOR LEETCODE" → only LeetCode tasks use it (it once became the GLOBAL default
            sp = dict(settings.get("site_profiles") or {})           # and every browser task ran in the owner's
            if sp.get(site) != profile:                              # logged-in profile)
                sp[site] = profile
                settings.update({"site_profiles": sp})
                ctx.emit(f"Saved: use browser profile '{profile}' for {site}")
        elif profile != (settings.get("browser_profile") or ""):
            settings.update({"browser_profile": profile})       # only when the user asked to make it the default
            ctx.emit(f"Saved browser profile '{profile}' as your default")
    if mode == "extension":
        return _ext_open(ctx)
    try:
        body = {"profile": profile, "mode": "sandbox"}
        if mode == "host":
            r = bridge("POST", "/chrome/launch", json={"profile_name": profile, "cdp_public": config.BRIDGE_CDP_PUBLIC})
            if not isinstance(r, dict) or "error" in r or "cdp_url" not in r:
                return r if isinstance(r, dict) else {"error": str(r)}
            body = {"profile": profile, "mode": "cdp", "cdp_url": r["cdp_url"]}
            act_ = bridge("GET", "/chrome/active")                # verify BEFORE acting: is it really that profile?
            if isinstance(act_, dict) and act_.get("profile") not in (None, profile):
                return {"error": f"Chrome is running profile '{act_.get('profile')}', not the requested '{profile}'",
                        "profile": profile}
            ctx.emit("Attached to your Chrome (profile " + profile + (" — switched" if r.get("switched") else "")
                     + ")", status="info", host_browser=True, profile=profile)
        r = browser("POST", "/session", json=body)
    except ServiceUnavailable as e:
        return {"error": str(e)}
    if isinstance(r, dict) and "session_id" in r:
        _put(ctx, {"session_id": r["session_id"], "mode": mode, "profile": profile})
        r = {**r, "mode": mode}
    return {**r, "profile": profile} if isinstance(r, dict) else r


def _vm_open(ctx, profile: str) -> dict:
    """Session in the test VM's browser: its own logged-in profiles, hidden display, never the owner's screen."""
    from .. import vmbrowser
    try:
        r = vmbrowser.call("POST", "/session", json={"profile": profile or "default", "mode": "sandbox"})
    except ServiceUnavailable as e:
        return {"error": f"VM browser unavailable ({e}) — start it: wsl -d agentos-vm bash /opt/agentos/scripts/vm_up.sh"}
    if not isinstance(r, dict) or "session_id" not in r:
        return r if isinstance(r, dict) else {"error": str(r)}
    _put(ctx, {"session_id": r["session_id"], "mode": "vm", "profile": profile, "svc": "vm"})
    ctx.emit(f"Using the VM browser (profile {profile or 'default'}) — not your screen. Watch: {vmbrowser.view_url()}",
             status="info", vm_browser=True, profile=profile)
    return {**r, "mode": "vm", "profile": profile}


_AGENT_PATH = re.compile(r"^(?:file://)?(/(?:workspace|shared|mounts|tmp)/.+)$")


def _local_file_url(url: str) -> str:
    """'/workspace/site/index.html' (or file:///workspace/...) → the real file:// URL the browser can open. Agents
    used to guess http://localhost:<port>/index.html for files they had just written (16.10: 10 wasted steps)."""
    m = _AGENT_PATH.match(url.strip())
    if not m:
        return url
    from ..clients import runner
    try:
        r = runner("GET", "/fs/realpath", params={"path": m.group(1)}, timeout=10)
        return r.get("file_url", url) if isinstance(r, dict) else url
    except ServiceUnavailable:
        return url


@tool("browser_act", "Perform a browser action: goto{url} (a web URL, or a file you wrote: goto{url:'/workspace/site/index.html'}), click{ref|selector}, type{ref|selector,text,submit}, "
      "press{key}, set_code{text, language} (put code into the page's code editor, e.g. LeetCode; language e.g. 'Python3' switches the editor's language picker first), select{selector,value}, scroll{dy}, back, wait{ms}, tab_new{url} (ALWAYS use this to open a tab — keyboard shortcuts like Ctrl+T do not work), tab_close{index}, tab_switch{index}. The snapshot's `tabs` list shows the real open tabs — check it before saying tabs were opened.",
      {"action": {"type": "string"}, "url": {"type": "string", "default": ""}, "ref": {"type": "string", "default": ""},
       "selector": {"type": "string", "default": ""}, "text": {"type": "string", "default": ""},
       "key": {"type": "string", "default": ""}, "value": {"type": "string", "default": ""},
       "submit": {"type": "boolean", "default": False}, "dy": {"type": "integer", "default": 600},
       "ms": {"type": "integer", "default": 1000}, "index": {"type": "integer", "default": 0},
       "ref_name": {"type": "string", "default": ""}, "language": {"type": "string", "default": ""}},
      needs="browser", risky=_risk_act)
def browser_act(ctx, action: str, **kw):
    try:
        s = _session(ctx)
        if "error" in s:
            return s
        if s.get("mode") == "extension":
            return _ext_act(ctx, s, action, kw)
        body = {"session_id": s["session_id"], "action": action, **{k: v for k, v in kw.items() if v not in ("", None)}}
        body.pop("ref_name", None)
        if action in ("goto", "tab_new") and body.get("url"):
            body["url"] = _local_file_url(body["url"])
        return _svc(s, "POST", "/act", json=body)
    except ServiceUnavailable as e:
        return {"error": str(e)}


@tool("browser_snapshot", "Get the current page: url, title, interactive elements with refs, visible text.", {},
      needs="browser")
def browser_snapshot(ctx):
    try:
        s = _session(ctx)
        if "error" in s:
            return s
        if s.get("mode") == "extension":
            return _ext_call(s, "snapshot", {"tab_id": s["tab_id"]})
        return _svc(s, "GET", "/snapshot", params={"session_id": s["session_id"]})
    except ServiceUnavailable as e:
        return {"error": str(e)}


@tool("browser_screenshot", "Screenshot of the current page (base64 PNG).", {}, needs="browser")
def browser_screenshot(ctx):
    try:
        s = _session(ctx)
        if "error" in s:
            return s
        if s.get("mode") == "extension":
            r = _ext_call(s, "screenshot", {"tab_id": s["tab_id"]})
            if isinstance(r, dict) and r.get("png_base64"):
                from .shots import save_png
                return {**save_png(ctx, r["png_base64"], "tab"), "png_base64": r["png_base64"]}
            return r
        png = _svc(s, "GET", "/screenshot", params={"session_id": s["session_id"]})
        if isinstance(png, (bytes, bytearray)):
            from .shots import save_png
            b64 = base64.b64encode(png).decode()
            return {**save_png(ctx, b64, "browser"), "png_base64": b64}
        return png
    except ServiceUnavailable as e:
        return {"error": str(e)}
