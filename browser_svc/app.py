"""Browser service — see docs/CONTRACTS.md section 4.

Two modes:
* sandbox: Playwright Chromium, a persistent context per profile under PROFILES_DIR/<name>. It runs headful on
  the container's Xvfb display, so you can watch and take over through noVNC.
* cdp: attach to an already-running Chrome, e.g. your own Chrome launched by the Host Bridge, via connect_over_cdp.

Env: PROFILES_DIR (default /profiles), BROWSER_HEADLESS (default "false"), BROWSER_PROXY (e.g. http://egress:3128)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

@asynccontextmanager
async def _lifespan(_app):
    yield
    await shutdown()


app = FastAPI(title="AgentOS browser", version="1.0", lifespan=_lifespan)

PROFILES_DIR = Path(os.environ.get("PROFILES_DIR", "/profiles"))
HEADLESS = os.environ.get("BROWSER_HEADLESS", "false").lower() == "true"
PROXY = os.environ.get("BROWSER_PROXY", "")
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

_state: dict[str, Any] = {"pw": None, "contexts": {}, "cdp": {}, "sessions": {}, "takeover": False}
_lock = asyncio.Lock()

# Injected into the page: tags interactive, visible elements with data-aos-ref and returns a compact list.
_SNAPSHOT_JS = r"""
() => {
  const sel = 'a,button,input,select,textarea,summary,[role],[onclick],[contenteditable="true"],[tabindex]:not([tabindex="-1"])';
  document.querySelectorAll('[data-aos-ref]').forEach(e => e.removeAttribute('data-aos-ref'));
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || st.visibility === 'hidden' || st.display === 'none') continue;
    // closed menus/drawers often stay in the DOM: invisible/transparent ancestors, aria-hidden/inert, slid off-screen.
    // Listing them made the agent "close" overlays that weren't open (LeetCode T1: ~20 wasted steps).
    if (el.checkVisibility && !el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    if (el.closest('[aria-hidden="true"],[inert]')) continue;
    if (r.right <= 0 || r.left >= window.innerWidth) continue;
    if (n >= 300) break;
    const ref = String(++n); el.setAttribute('data-aos-ref', ref);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || ({a:'link',button:'button',select:'combobox',textarea:'textbox',summary:'button'}[tag])
               || (tag === 'input' ? ((el.type === 'checkbox' || el.type === 'radio') ? el.type : (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox') : tag);
    // icon-only buttons: fall back to the site's own test/automation ids (LeetCode Run/Submit/Debug were 3 unnamed
    // buttons — the agent pressed Debug thinking it was Run)
    const name = (el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('title') ||
                  el.getAttribute('placeholder') || el.getAttribute('name') || el.getAttribute('data-e2e-locator') ||
                  el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-cy') ||
                  (el.querySelector && el.querySelector('svg title') ? el.querySelector('svg title').textContent : '') ||
                  '').trim();
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const item = {ref, role, name: name.slice(0, 120), tag, text, y: Math.round(r.top),
                  inview: r.top < window.innerHeight && r.bottom > 0};
    if (tag === 'a' && el.href) item.href = el.href.slice(0, 200);
    if (tag === 'input') item.value = String(el.value || '').slice(0, 80);
    out.push(item);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 8000);
  return {elements: out, text};
}
"""


async def _pw():
    if _state["pw"] is None:
        from playwright.async_api import async_playwright
        _state["pw"] = await async_playwright().start()
    return _state["pw"]


async def _sandbox_context(profile: str):
    ctx = _state["contexts"].get(profile)
    if ctx is not None:
        return ctx
    if _manual_alive(profile):
        raise HTTPException(409, f"the owner is logging in to profile '{profile}' on the VM screen — try again after "
                                 "they press Done")
    pw = await _pw()
    d = PROFILES_DIR / profile
    d.mkdir(parents=True, exist_ok=True)
    # no "controlled by automation" markers (navigator.webdriver, --enable-automation): bot checks such as
    # Cloudflare's blocked the owner's own logged-in profile (Codeforces showed "verifying you are not a bot")
    kw: dict[str, Any] = {"headless": HEADLESS, "viewport": {"width": 1280, "height": 800},
                          "args": ["--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage",
                                   "--disable-blink-features=AutomationControlled"],
                          "ignore_default_args": ["--enable-automation"]}
    if PROXY:
        kw["proxy"] = {"server": PROXY}
    try:
        ctx = await pw.chromium.launch_persistent_context(str(d), **kw)
    except Exception as e:  # noqa: BLE001 - a profile folder another Chrome holds → a clear 409, not a 500
        if "SingletonLock" in str(e) or "ProcessSingleton" in str(e):
            raise HTTPException(409, f"profile '{profile}' is open in another Chrome window (e.g. a login window "
                                     "on the VM screen) — close it first") from e
        raise
    ctx.on("close", lambda *_: _state["contexts"].pop(profile, None))
    _state["contexts"][profile] = ctx
    return ctx


async def _cdp_context(cdp_url: str):
    br = _state["cdp"].get(cdp_url)
    if br is None or not br.is_connected():
        pw = await _pw()
        br = await pw.chromium.connect_over_cdp(cdp_url)
        _state["cdp"][cdp_url] = br
    return br.contexts[0] if br.contexts else await br.new_context()


def _session(session_id: str) -> dict:
    s = _state["sessions"].get(session_id)
    if not s:
        raise HTTPException(404, f"unknown session {session_id}")
    return s


async def _page(s: dict):
    page = s.get("page")
    if page is None or page.is_closed():
        pages = [p for p in s["context"].pages if not p.is_closed()]
        page = pages[-1] if pages else await s["context"].new_page()
        s["page"] = page
    return page


# ---------------------------------------------------------------------------------------------- models
class SessionReq(BaseModel):
    profile: str = "default"
    mode: str = "sandbox"          # sandbox | cdp
    cdp_url: Optional[str] = None


class ActReq(BaseModel):
    session_id: str
    action: str
    url: Optional[str] = None
    selector: Optional[str] = None
    ref: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    text: Optional[str] = None
    submit: bool = False
    key: Optional[str] = None
    value: Optional[str] = None
    dy: Optional[float] = None
    ms: Optional[int] = None
    index: Optional[int] = None
    expr: Optional[str] = None
    texts: Optional[list[str]] = None     # wait_for: any of these texts on the page
    language: Optional[str] = None        # set_code: switch the editor's language picker to this first (e.g. Python3)
    new_only: bool = False                # wait_for: only text that appeared after the last click counts
    timeout_s: Optional[int] = None


class TakeoverReq(BaseModel):
    on: bool


# ---------------------------------------------------------------------------------------------- routes
@app.get("/health")
async def health() -> dict:
    return {"ok": True, "sessions": len(_state["sessions"]), "takeover": _state["takeover"], "headless": HEADLESS}


@app.get("/profiles")
async def profiles() -> dict:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir())
    return {"profiles": names or ["default"]}


@app.post("/session")
async def new_session(req: SessionReq) -> dict:
    async with _lock:
        if req.mode == "cdp":
            if not req.cdp_url:
                raise HTTPException(400, "cdp_url required for mode=cdp")
            try:
                ctx = await _cdp_context(req.cdp_url)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"CDP connect failed: {e}")
        else:
            if not _PROFILE_RE.match(req.profile):
                raise HTTPException(400, "profile name must match [A-Za-z0-9_-]{1,40}")
            ctx = await _sandbox_context(req.profile)
        sid = uuid.uuid4().hex[:10]
        s = {"id": sid, "profile": req.profile, "mode": req.mode, "context": ctx, "page": None}
        _state["sessions"][sid] = s
        if req.mode == "cdp":  # the user's own Chrome: work in a fresh tab, never hijack the tab they are using
            s["page"] = await ctx.new_page()
            try:
                await s["page"].bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        else:   # a tab no ACTIVE session owns (the last one a finished step released), else a new tab — two
            owned = {id(x["page"]) for x in _state["sessions"].values()    # parallel steps never share a tab
                     if x is not s and x["context"] is ctx and x.get("page") is not None and not x.get("released")}
            free = [p for p in ctx.pages if not p.is_closed() and id(p) not in owned]
            if free:
                s["page"] = free[-1]
            else:
                s["page"] = await ctx.new_page()
                s.setdefault("opened", []).append(s["page"])
        page = await _page(s)
        return {"session_id": sid, "url": page.url}


def _target(req: ActReq) -> str:
    if req.ref:
        return f'[data-aos-ref="{re.sub(r"[^0-9]", "", req.ref)}"]'
    if req.selector:
        return req.selector
    raise HTTPException(400, "selector or ref required")


class CloseReq(BaseModel):
    session_id: str
    close_tabs: bool | None = None   # default: sandbox → close its tabs; your Chrome (cdp) → keep them open


@app.post("/session/close")
async def close_session(req: CloseReq) -> dict:
    """End a session and free memory: close the tabs it opened; close an idle sandbox Chromium entirely."""
    async with _lock:
        s = _state["sessions"].pop(req.session_id, None)
        if not s:
            return {"ok": True, "closed": 0}
        close_tabs = req.close_tabs if req.close_tabs is not None else s["mode"] != "cdp"
        closed = 0
        if close_tabs:
            for pg in s.get("opened", []) + ([s["page"]] if s.get("page") and s["mode"] != "cdp" else []):
                try:
                    if not pg.is_closed():
                        await pg.close()
                        closed += 1
                except Exception:  # noqa: BLE001
                    pass
        if s["mode"] != "cdp" and not any(x["context"] is s["context"] for x in _state["sessions"].values()):
            try:
                await s["context"].close()   # idle persistent profile: release the whole Chromium (logins stay on disk)
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "closed": closed}


@app.post("/act")
async def act(req: ActReq) -> dict:
    if _state["takeover"]:
        raise HTTPException(423, "user has taken over the browser; wait for hand-back")
    s = _session(req.session_id)
    page = await _page(s)
    a = req.action
    k = (req.key or "").lower().replace(" ", "")
    if a == "press" and k in ("control+t", "ctrl+t", "meta+t"):
        a = "tab_new"            # browser shortcuts don't reach Chrome through automation: do it for real
    elif a == "press" and k in ("control+w", "ctrl+w", "meta+w"):
        a = "tab_close"
    try:
        if a == "goto":
            if not req.url:
                raise HTTPException(400, "url required")
            url = req.url if re.match(r"^(https?|data|about|file|chrome):", req.url, re.I) else "https://" + req.url
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        elif a == "click":
            try:   # what the page said BEFORE this click — wait_for{new_only} counts only text that appeared after it
                s["pre_click_text"] = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:  # noqa: BLE001
                s["pre_click_text"] = ""
            if req.x is not None and req.y is not None:
                await page.mouse.click(req.x, req.y)
            else:
                await page.click(_target(req), timeout=15000)
        elif a == "type":
            t = _target(req)
            await page.fill(t, req.text or "", timeout=15000)
            if req.submit:
                await page.press(t, "Enter")
        elif a == "set_code" and req.language:
            # code judges keep a per-account default language (T1: Python pasted while the picker said C++ → 4×
            # Compile Error). Switch the picker FIRST — switching resets the editor to a template.
            lang = await _switch_language(page, req.language)
            if lang.get("found") and (lang.get("current") or "").lower() != req.language.lower():
                return {"ok": False, "url": page.url, "title": await _safe_title(page), "language": lang,
                        "error": f"could not switch the editor language to {req.language} (it shows "
                                 f"'{lang.get('current')}') — pick it in the language dropdown first"}
            req = req.model_copy(update={"language": None})
            await _editor_settled(page)   # a language switch loads the site's template ASYNCHRONOUSLY: let it land
            r = await act(req)            # first, else it overwrites the code (T1 submitted the empty template)
            for _ in range(2):            # re-check after a pause; put the code back if something replaced it
                await page.wait_for_timeout(1500)
                back = await page.evaluate(_EDITOR_VALUE_JS) or ""
                if back.strip()[:200] == (req.text or "").strip()[:200]:
                    break
                r = await act(req)
            else:
                r = {**r, "verified": False, "error": "the editor kept replacing the code (template reload)"}
            return {**r, "language": lang.get("current") or "unknown"}
        elif a == "set_code":
            # code editors (Monaco on LeetCode, CodeMirror, Ace) ignore fill(); set the model value directly
            ok = await page.evaluate("""(code) => {
              try { const m = window.monaco && window.monaco.editor.getModels(); if (m && m.length) { m[0].setValue(code); return 'monaco'; } } catch (e) {}
              try { const cm = document.querySelector('.CodeMirror'); if (cm && cm.CodeMirror) { cm.CodeMirror.setValue(code); return 'codemirror'; } } catch (e) {}
              try { const v = document.querySelector('.cm-content'); if (v && v.cmView) { const view = v.cmView.view; view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: code}}); return 'cm6'; } } catch (e) {}
              try { const ace = window.ace && document.querySelector('.ace_editor'); if (ace) { window.ace.edit(ace).setValue(code, 1); return 'ace'; } } catch (e) {}
              const ta = document.querySelector('textarea'); if (ta) { ta.value = code; ta.dispatchEvent(new Event('input', {bubbles: true})); return 'textarea'; }
              return '';
            }""", req.text or "")
            if not ok:
                raise HTTPException(400, "no code editor found on this page")
            # read it back: verified result, not just an attempted action
            back = await page.evaluate("""() => {
              try { const m = window.monaco && window.monaco.editor.getModels(); if (m && m.length) return m[0].getValue(); } catch (e) {}
              try { const cm = document.querySelector('.CodeMirror'); if (cm && cm.CodeMirror) return cm.CodeMirror.getValue(); } catch (e) {}
              try { const v = document.querySelector('.cm-content'); if (v) return v.innerText; } catch (e) {}
              const ta = document.querySelector('textarea'); return ta ? ta.value : '';
            }""")
            same = (back or "").strip()[:200] == (req.text or "").strip()[:200]
            return {"ok": True, "url": page.url, "title": await page.title(), "editor": ok, "verified": bool(same),
                    "editor_text": (back or "")[:2000]}
        elif a == "press":
            await page.keyboard.press(req.key or "Enter")
        elif a == "select":
            await page.select_option(_target(req), req.value or "", timeout=15000)
        elif a == "scroll":
            await page.mouse.wheel(0, req.dy if req.dy is not None else 600)
        elif a == "back":
            await page.go_back(wait_until="domcontentloaded")
        elif a == "wait":
            await page.wait_for_timeout(min(req.ms or 1000, 30000))
        elif a == "wait_for":   # OBSERVE until a verdict text shows up (e.g. 'Accepted'); one call, no re-polling by the agent
            want = [t for t in (req.texts or [req.text or ""]) if t and t.strip()]
            if not want:
                raise HTTPException(400, "texts required")
            deadline = time.monotonic() + max(1, min(int(req.timeout_s or 30), 45))
            # new_only: a verdict counts only if it appears MORE often than before the last click (T1: the problem
            # page's own "Accepted 15.2M" statistic was read as the verdict of a submission that had failed)
            base = (s.get("pre_click_text") or "").lower() if req.new_only else ""
            while True:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                low = body.lower()
                hit = next((t for t in want if t.lower() in low and low.count(t.lower()) > base.count(t.lower())), None)
                if hit:
                    i = low.rindex(hit.lower())   # the newest occurrence (the verdict, not an older mention)
                    return {"ok": True, "url": page.url, "title": await _safe_title(page), "found": hit,
                            "details": body[max(0, i - 200):i + 400], "verified": True}
                if time.monotonic() > deadline:
                    return {"ok": False, "url": page.url, "title": await _safe_title(page), "found": "", "verified": False,
                            "error": f"none of {want} appeared within {req.timeout_s or 30}s"}
                await page.wait_for_timeout(1000)
        elif a == "tab_new":
            page = await s["context"].new_page()
            s.setdefault("opened", []).append(page)     # tabs this session created (closed with the session)
            s["page"] = page
            if req.url:
                await page.goto(req.url, wait_until="domcontentloaded", timeout=45000)
        elif a == "tab_close":
            pages = [p for p in s["context"].pages if not p.is_closed()]
            target = pages[req.index] if req.index is not None and 0 <= req.index < len(pages) else page
            await target.close()
            rest = [p for p in s["context"].pages if not p.is_closed()]
            if rest:
                s["page"] = rest[-1]
                page = rest[-1]
        elif a == "tab_switch":
            pages = [p for p in s["context"].pages if not p.is_closed()]
            if req.index is None or not 0 <= req.index < len(pages):
                raise HTTPException(400, f"index must be 0..{len(pages) - 1}")
            page = pages[req.index]
            s["page"] = page
            await page.bring_to_front()
        elif a == "eval_readonly":
            # Read-only by convention: the gateway only exposes this to agents with browser permission and the
            # expression result is serialised + capped. It is NOT a security boundary (the page is untrusted anyway).
            val = await page.evaluate(f"() => {{ return ({req.expr or 'null'}); }}")
            return {"ok": True, "url": page.url, "title": await page.title(),
                    "value": json.dumps(val, default=str)[:8000]}
        else:
            raise HTTPException(400, f"unknown action {a}")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "url": page.url, "title": await page.title()}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url": page.url, "title": await _safe_title(page), "error": f"{type(e).__name__}: {e}"[:500]}


_LANG_JS = r"""
(want) => {
  const names = ['C++','C','C#','Java','Python','Python3','JavaScript','TypeScript','PHP','Swift','Kotlin','Dart',
                 'Go','Ruby','Scala','Rust','Racket','Erlang','Elixir','Cangjie'];
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 &&
                      (!el.checkVisibility || el.checkVisibility({opacityProperty: true, visibilityProperty: true})); };
  const txt = el => (el.innerText || '').trim();
  const picker = [...document.querySelectorAll('button,[role=button],[role=combobox]')]
                   .find(b => vis(b) && names.includes(txt(b)));
  if (!picker) return {found: false};
  if (want === null) return {found: true, current: txt(picker)};
  if (txt(picker).toLowerCase() === want.toLowerCase()) return {found: true, current: txt(picker), switched: false};
  picker.click();
  return {found: true, current: txt(picker), opened: true};
}
"""

_LANG_OPTION_JS = r"""
(want) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const opts = [...document.querySelectorAll('div,li,button,span,[role=option],[role=menuitem]')]
                 .filter(el => vis(el) && (el.innerText || '').trim().toLowerCase() === want.toLowerCase());
  if (!opts.length) return false;
  opts.sort((a, b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height -
                      b.getBoundingClientRect().width * b.getBoundingClientRect().height);
  opts[0].click();
  return true;
}
"""


_EDITOR_VALUE_JS = r"""
() => {
  try { const m = window.monaco && window.monaco.editor.getModels(); if (m && m.length) return m[0].getValue(); } catch (e) {}
  try { const cm = document.querySelector('.CodeMirror'); if (cm && cm.CodeMirror) return cm.CodeMirror.getValue(); } catch (e) {}
  try { const v = document.querySelector('.cm-content'); if (v) return v.innerText; } catch (e) {}
  const ta = document.querySelector('textarea'); return ta ? ta.value : '';
}
"""


async def _editor_settled(page, quiet_ms: int = 1200, max_ms: int = 6000) -> None:
    """Wait until the code editor's content stops changing (≤6 s)."""
    last, still, waited = None, 0, 0
    while waited < max_ms:
        v = await page.evaluate(_EDITOR_VALUE_JS)
        still = still + 300 if v == last else 0
        if still >= quiet_ms:
            return
        last = v
        await page.wait_for_timeout(300)
        waited += 300


async def _switch_language(page, want: str) -> dict:
    """Make the code editor's language picker show `want` (no picker on the page → {'found': False}, nothing done)."""
    r = await page.evaluate(_LANG_JS, want)
    if not r.get("found") or not r.get("opened"):
        return r
    await page.wait_for_timeout(600)
    if not await page.evaluate(_LANG_OPTION_JS, want):
        return {**r, "error": f"option '{want}' not found in the language list"}
    await page.wait_for_timeout(900)
    return await page.evaluate(_LANG_JS, None)


async def _safe_title(page) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001
        return ""


class ReleaseReq(BaseModel):
    session_id: str


@app.post("/session/release")
async def release_session(req: ReleaseReq) -> dict:
    """A step finished: its tab stays open (state, logins, the page it reached) for the next step to continue on."""
    s = _state["sessions"].get(req.session_id)
    if s:
        s["released"] = True
    return {"ok": bool(s)}


@app.get("/snapshot")
async def snapshot(session_id: str = Query(...)) -> dict:
    s = _session(session_id)
    page = await _page(s)
    # single-page apps fill in AFTER "loaded": an empty page is usually still rendering — wait (≤6 s) for content
    # instead of reporting nothing (T1: the agent saw a blank LeetCode problem list and wandered off)
    deadline = time.monotonic() + 6
    while True:
        try:
            data = await page.evaluate(_SNAPSHOT_JS)
        except Exception as e:  # noqa: BLE001 - navigating mid-read: retry until the deadline
            data = {"elements": [], "text": "", "error": str(e)[:300]}
        if (data.get("elements") and len(data.get("text") or "") > 40) or time.monotonic() > deadline \
                or page.url.startswith(("about:", "chrome-error:")):
            break
        await page.wait_for_timeout(400)
    tabs = [p.url for p in s["context"].pages if not p.is_closed()]
    return {"url": page.url, "title": await _safe_title(page), "tabs": tabs, **data}


@app.get("/screenshot")
async def screenshot(session_id: str = Query(...)) -> Response:
    s = _session(session_id)
    page = await _page(s)
    # In CDP mode (the user's own Chrome) the agent's tab may be in the background, where Chrome stops painting and
    # page.screenshot() waits for a frame forever. Bring it to front, then fall back to a raw CDP capture.
    try:
        await page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    try:
        png = await page.screenshot(type="png", timeout=12000, animations="disabled", caret="initial")
    except Exception as first:  # noqa: BLE001
        try:
            import base64
            cdp = await s["context"].new_cdp_session(page)
            try:
                shot = await cdp.send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            finally:
                await cdp.detach()
            png = base64.b64decode(shot["data"])
        except Exception as second:  # noqa: BLE001
            raise HTTPException(504, f"screenshot failed: {first}; CDP fallback: {second}"[:400])
    return Response(content=png, media_type="image/png")


class ManualReq(BaseModel):
    profile: str
    urls: list[str] = []
    extension_token: Optional[str] = None   # load the AgentOS extension into this window, pre-paired (VM test setups)
    extension_url: Optional[str] = None     # gateway socket, e.g. ws://127.0.0.1:7870/ws/extension


def _prepare_extension(profile: str, token: str, url: str | None) -> str:
    """Copy extension/ to <profiles>/../extensions/<profile> + a config.json (token, url, profile): the extension
    pairs itself, so the profile shows up on the Browser page without anyone pasting a token. '' if no sources."""
    import shutil
    src = Path(os.environ.get("EXTENSION_SRC") or Path(__file__).resolve().parent.parent / "extension")
    if not (src / "manifest.json").is_file():
        return ""
    dest = PROFILES_DIR.parent / "extensions" / profile
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("README.md", "config.json"))
    (dest / "config.json").write_text(json.dumps({"token": token, "url": url or "", "profile": profile}), encoding="utf-8")
    return str(dest)


_manual: dict[str, Any] = {}   # profile -> Popen of a PLAIN Chrome the owner logs in with


def _manual_alive(profile: str) -> bool:
    p = _manual.get(profile)
    return bool(p) and p.poll() is None


@app.post("/manual")
async def manual_open(req: ManualReq) -> dict:
    """Open `profile` in a plain Chrome with NO automation attached, so the owner can log in (Google refuses sign-in
    in automated browsers: 'This browser or app may not be secure'). Linux test VM only (hidden display, watched via
    noVNC). Logins land in the same profile folder; agents use them afterwards. Calling again adds tabs."""
    import subprocess
    if os.name == "nt":
        raise HTTPException(400, "manual login windows are only for the Linux test VM")
    if not _PROFILE_RE.match(req.profile):
        raise HTTPException(400, "profile name must match [A-Za-z0-9_-]{1,40}")
    async with _lock:   # Chrome locks a profile folder: release our automated window on it first
        for sid, s in list(_state["sessions"].items()):
            if s["profile"] == req.profile and s["mode"] != "cdp":
                _state["sessions"].pop(sid, None)
        ctx = _state["contexts"].pop(req.profile, None)
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:  # noqa: BLE001
                pass
    pw = await _pw()
    d = PROFILES_DIR / req.profile
    d.mkdir(parents=True, exist_ok=True)
    # same password store as Playwright's own launches, so the saved cookies stay readable by the agents later
    ext = _prepare_extension(req.profile, req.extension_token, req.extension_url) if req.extension_token else ""
    args = [pw.chromium.executable_path, f"--user-data-dir={d}", "--no-first-run", "--no-default-browser-check",
            "--password-store=basic", "--use-mock-keychain", "--disable-dev-shm-usage", "--start-maximized",
            *([f"--load-extension={ext}", f"--disable-extensions-except={ext}"] if ext else []),
            *(["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []),   # root in the VM
            *[u for u in req.urls if re.match(r"^https?://", u)]]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if not _manual_alive(req.profile):   # a second call only forwards its URLs to the running window as new tabs
        _manual[req.profile] = p
    return {"ok": True, "profile": req.profile, "pid": _manual[req.profile].pid}


@app.post("/manual/close")
async def manual_close(req: ManualReq) -> dict:
    p = _manual.pop(req.profile, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except Exception:  # noqa: BLE001
            p.kill()
    return {"ok": True, "closed": bool(p)}


@app.post("/takeover")
async def takeover(req: TakeoverReq) -> dict:
    _state["takeover"] = req.on
    return {"ok": True, "takeover": req.on}


async def shutdown() -> None:
    for ctx in list(_state["contexts"].values()):
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
    _state["contexts"].clear()
    _state["sessions"].clear()
    if _state["pw"]:
        await _state["pw"].stop()
        _state["pw"] = None
