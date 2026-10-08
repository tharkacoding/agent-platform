"""Real Windows desktop control through the host bridge: one approval per task; the bridge refuses input while the
owner is using the PC or when the target window is not in front."""
import base64
import re

from .. import registry
from ..clients import ServiceUnavailable, bridge
from ..core import approvals
from ..core.sdk import ApprovalDenied, tool


@tool("desktop_screenshot", "Screenshot of the real Windows desktop (base64 PNG).", {}, needs="desktop")
def desktop_screenshot(ctx):
    try:
        png = bridge("GET", "/desktop/screenshot")
    except ServiceUnavailable as e:
        return {"error": str(e)}
    if not isinstance(png, (bytes, bytearray)):
        return png
    from .shots import save_png
    b64 = base64.b64encode(png).decode()
    return {**save_png(ctx, b64, "screen"), "png_base64": b64}


import threading
import time

DESKTOP_LOCK = threading.Lock()   # one agent at a time drives the real mouse/keyboard (computer use is serial)


@tool("desktop_tree", "Read a window via UI Automation: elements with refs, names, roles, screen rects. "
      "`window` = part of the title (e.g. 'LeetCode - Google Chrome'); `contains` picks the window whose inner pane "
      "mentions it — use '- <profile>' to target a specific Chrome profile (Chrome shows the profile there).",
      {"window": {"type": "string", "default": ""}, "contains": {"type": "string", "default": ""}}, needs="desktop")
def desktop_tree(ctx, window: str = "", contains: str = ""):
    try:
        return bridge("GET", "/desktop/tree", params={"window": window, "contains": contains}, timeout=60)
    except ServiceUnavailable as e:
        return {"error": str(e)}


def wait_until_idle(ctx, max_wait_s: int = 900) -> dict:
    """Computer use never fights the user: wait (up to 15 min) until they've stepped away from the PC."""
    deadline, told = time.time() + max_wait_s, False
    while True:
        try:
            idle = bridge("GET", "/desktop/idle", timeout=10)
        except ServiceUnavailable as e:
            return {"error": str(e)}
        if not (isinstance(idle, dict) and idle.get("owner_active")):
            return {"ok": True}
        if not told:
            ctx.emit("Waiting for you to step away from the computer before taking over the mouse/keyboard…",
                     status="waiting")
            told = True
        if time.time() > deadline:
            return {"error": "you kept using the computer — gave up waiting (nothing was done)", "owner_active": True}
        ctx.run.check_cancel()
        time.sleep(5)


def _session_approval(ctx) -> str:
    """ONE approval per task for computer control (re-used for that task's actions; idle + foreground checks
    still run on every single action in the bridge)."""
    title = f"desktop_act: let {ctx.spec.name} control your mouse & keyboard for this task"
    if not approvals.request(ctx.run, ctx.spec.id, title, {"task": ctx.task.instruction[:300]}):
        raise ApprovalDenied(title)
    return next((a["id"] for a in registry.list_approvals("approved") if a["title"] == title
                 and a["run_id"] == ctx.run.run_id), "")


@tool("desktop_act", "Act on the real desktop: click/double_click{ref|x,y}, type{text}, paste{text} (exact text via "
      "clipboard — best for code), paste_at{x,y,text} (click that point, select all, paste — replaces the content "
      "of an editor/text box in ONE step), open_url{url} (go to a page in the window in front: ctrl+L, paste the URL, "
      "Enter, then checks the address bar), hotkey{keys e.g. 'ctrl+a'}, focus{ref='w0' = the window from the last "
      "desktop_tree}, launch{app}. Refs come from the latest desktop_tree. One approval per task; refused while the "
      "user is using the computer or if the target window isn't in front.",
      {"action": {"type": "string"}, "ref": {"type": "string", "default": ""}, "x": {"type": "integer", "default": -1},
       "y": {"type": "integer", "default": -1}, "text": {"type": "string", "default": ""},
       "keys": {"type": "string", "default": ""}, "app": {"type": "string", "default": ""}}, needs="desktop")
def desktop_act(ctx, action: str, **kw):
    args = {k: v for k, v in kw.items() if v not in ("", None, -1)}
    if action == "launch" and "chrome" in str(args.get("app", "")).lower():
        # raw `chrome --profile-directory=<name>` uses folder names (asdf = "Profile 6") and can CREATE an empty
        # profile; real profiles are only opened through chrome_open_profile, which maps the name to its folder
        return {"error": "Don't launch Chrome directly. Use chrome_open_profile(profile=<name>, url=<url>) — it opens "
                         "the right real profile (and tab) safely."}
    aid = _session_approval(ctx)
    if action == "open_url":   # compound: address bar ← URL via clipboard (never typed key by key), verified
        url = str(args.get("url") or args.get("text") or "").strip()
        if not url:
            return {"error": "open_url needs url"}
        for attempt in range(2):
            for sub in ({"action": "hotkey", "keys": "ctrl+l"}, {"action": "paste", "text": url},
                        {"action": "hotkey", "keys": "enter"}):
                r = _act_once(ctx, aid, sub)
                if isinstance(r, dict) and r.get("error"):
                    return {"error": f"open_url failed at {sub['action']}: {r['error']}"}
                time.sleep(0.25)
            time.sleep(2.5)
            try:
                tr = bridge("GET", "/desktop/tree", timeout=60)
                bar = next((e.get("value", "") for e in (tr or {}).get("elements", []) if e.get("control_type") == "Edit"
                            and "address" in (e.get("name") or "").lower()), "") if isinstance(tr, dict) else ""
            except Exception:  # noqa: BLE001
                bar = ""
            if not bar or _same_page(bar, url) or _same_page(url, bar):
                return {"ok": True, "url": url, "address_bar": bar}
        return {"error": f"address bar shows '{bar}' instead of {url}"}
    if action == "paste_at":   # compound: click into the field, select everything, paste (deterministic, one step)
        if "x" not in args or "y" not in args:
            return {"error": "paste_at needs x and y (a point inside the editor/text box)"}
        for sub in ({"action": "click", "x": args["x"], "y": args["y"]}, {"action": "hotkey", "keys": "ctrl+a"},
                    {"action": "paste", "text": args.get("text", "")}):
            r = _act_once(ctx, aid, sub)
            if isinstance(r, dict) and r.get("error"):
                return {"error": f"paste_at failed at {sub['action']}: {r['error']}"}
            time.sleep(0.3)
        return {"ok": True, "pasted_chars": len(args.get("text", "")), "at": [args["x"], args["y"]]}
    return _act_once(ctx, aid, {"action": action, **args})


def _act_once(ctx, aid: str, payload: dict):
    with DESKTOP_LOCK:
        for _ in range(3):
            try:
                r = bridge("POST", "/desktop/act", approval_id=aid, json=payload, timeout=60)
            except ServiceUnavailable as e:
                return {"error": str(e)}
            if isinstance(r, dict) and "using the computer" in str(r.get("error", "")):
                w = wait_until_idle(ctx)          # the user came back mid-task: pause, then continue
                if w.get("error"):
                    return w
                continue
            return r
        return r


@tool("chrome_open_profile", "Open a URL in the user's REAL Chrome profile (by profile name, e.g. 'asdf') — where "
      "they are logged in. Normal Chrome (no automation port); drive it afterwards with desktop_tree/desktop_act "
      "using contains='- <profile>'. Refused while the user is actively using the computer.",
      {"profile": {"type": "string"}, "url": {"type": "string"}}, needs="desktop")
def chrome_open_profile(ctx, profile: str, url: str):
    if not _profile_requested(ctx, profile):   # MISTAKES.txt M2: never pick one of the owner's profiles on our own
        return {"error": f"The owner did not ask for Chrome profile '{profile}' in this task. Don't use a profile from "
                         "memory or old tasks. If they named no profile, open the page in their default browser "
                         f"with desktop_act launch{{app: '{url}'}}, or use the browser agent's own browser."}
    w = wait_until_idle(ctx)
    if w.get("error"):
        return w
    # already open in that profile's window (address bar shows it)? reuse it instead of piling up new tabs
    try:
        tr = bridge("GET", "/desktop/tree", params={"window": "Google Chrome", "contains": f"- {profile}"}, timeout=60)
        bar = next((e.get("value", "") for e in (tr or {}).get("elements", []) if e.get("control_type") == "Edit"
                    and "address" in (e.get("name") or "").lower()), "") if isinstance(tr, dict) else ""
        if bar and _same_page(bar, url):
            return {"ok": True, "profile": profile, "reused": True, "window_suffix": f"- {profile}",
                    "hint": f"the page is already open in '{profile}' — observe it with desktop_tree(window='Google "
                            f"Chrome', contains='- {profile}')"}
    except Exception:  # noqa: BLE001
        pass
    try:
        r = bridge("POST", "/chrome/open_real", json={"profile": profile, "url": url}, timeout=30)
    except ServiceUnavailable as e:
        return {"error": str(e)}
    if isinstance(r, dict) and r.get("ok"):
        r["hint"] = (f"now call desktop_tree(window='Google Chrome', contains='{r.get('window_suffix')}') to observe "
                     "this profile's window")
    return r


@tool("desktop_locate", "LOOK at the screen to find something that is NOT in desktop_tree (code editor text "
      "area, canvas, image, unlabeled control). Returns its center as screen coordinates x,y for click/paste_at.",
      {"what": {"type": "string"}, "window": {"type": "string", "default": ""}}, needs="desktop")
def desktop_locate(ctx, what: str, window: str = ""):
    # MISTAKES.txt M6: look at the TARGET window, not whatever happens to be in front
    focus = _act_once(ctx, _session_approval(ctx), {"action": "focus", "ref": "w0"})
    if isinstance(focus, dict) and focus.get("error"):
        return {"found": False, "error": f"could not bring the target window to the front: {focus['error']}"}
    time.sleep(0.4)
    try:
        png = bridge("GET", "/desktop/screenshot")
    except ServiceUnavailable as e:
        return {"error": str(e)}
    if not isinstance(png, (bytes, bytearray)):
        return png if isinstance(png, dict) else {"error": "no screenshot"}
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        W, H = im.size
        scale = min(1.0, 1280 / W)
        small = im.resize((int(W * scale), int(H * scale))) if scale < 1 else im
        buf = io.BytesIO()
        small.convert("RGB").save(buf, "PNG", optimize=True)
        b64, w, h = base64.b64encode(buf.getvalue()).decode(), small.width, small.height
    except Exception:  # noqa: BLE001
        b64, w, h, scale = base64.b64encode(png).decode(), 0, 0, 1.0
    d = ctx.llm().vision_json(
        "You locate UI targets in a screenshot. Return ONLY {\"found\": bool, \"x\": int, \"y\": int, "
        "\"note\": str} where x,y are PIXEL coordinates in THIS image of the CENTER of the target (for a text "
        "editor: a point inside its text area, on an empty or code line). found=false if it is not visible.",
        f"Image size: {w}x{h}. Target: {what}"
        + (f"\nOnly look inside the window whose title contains: {window}. If the target is not inside THAT window "
           "(e.g. another app or browser is in front), return found=false." if window else ""),
        b64, fallback=lambda: {})
    if not d.get("found") or d.get("x") is None:
        return {"found": False, "error": f"could not see '{what}' on screen", "note": d.get("note", "")}
    x, y = int(round(int(d["x"]) / scale)), int(round(int(d["y"]) / scale))
    return {"found": True, "x": x, "y": y, "note": str(d.get("note", ""))[:200],
            "hint": f"use paste_at{{x:{x},y:{y},text}} or click{{x:{x},y:{y}}}"}


def _profile_requested(ctx, profile: str) -> bool:
    """A real Chrome profile may be used only if the owner named it in THIS request (or a message sent while it
    runs), or saved it as a standing default ('always use asdf')."""
    from .. import registry, settings
    p = (profile or "").strip().lower()
    if not p:
        return False
    if p == (settings.get("browser_profile") or "").strip().lower() and p not in ("default", ""):
        return True
    texts = []
    try:
        texts.append((registry.get_run(ctx.run.run_id) or {}).get("request", ""))
    except Exception:  # noqa: BLE001
        pass
    texts += [m.get("text", "") for m in getattr(ctx.run, "steer", []) or []]
    blob = " ".join(texts).lower()
    return bool(re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", blob))


def _same_page(bar: str, url: str) -> bool:
    """Address bar 'leetcode.com/problems/two-sum/description/' vs 'https://leetcode.com/problems/two-sum/'."""
    import re as _re
    norm = lambda u: _re.sub(r"^(https?://)?(www\.)?", "", (u or "").strip().lower()).rstrip("/")  # noqa: E731
    a, b = norm(bar), norm(url)
    return bool(a and b) and (a == b or a.startswith(b + "/"))


@tool("desktop_wait_for", "OBSERVE until text appears in a window (e.g. a result like 'Accepted', 'Saved', "
      "'Sent'); returns which of the texts was seen and nearby details. Use it to VERIFY an action's outcome.",
      {"texts": {"type": "array", "items": {"type": "string"}}, "window": {"type": "string", "default": ""},
       "contains": {"type": "string", "default": ""}, "timeout_s": {"type": "integer", "default": 60}},
      needs="desktop")
def desktop_wait_for(ctx, texts: list, window: str = "", contains: str = "", timeout_s: int = 60):
    deadline = time.time() + max(5, min(int(timeout_s), 180))
    wanted = [str(t).strip().lower() for t in texts or [] if str(t).strip()]
    while time.time() < deadline:
        try:
            tr = bridge("GET", "/desktop/tree", params={"window": window, "contains": contains}, timeout=60)
        except ServiceUnavailable as e:
            return {"error": str(e)}
        els = (tr or {}).get("elements") or [] if isinstance(tr, dict) else []
        pane = next((e for e in els if e.get("control_type") == "Pane"), {})
        bottom = (pane.get("rect") or [0, 0, 0, 10000])[3]
        for i, el in enumerate(els):
            name = (el.get("name") or "").strip()
            r = el.get("rect") or [0, 0, 0, 0]
            if name.lower() in wanted and len(r) == 4 and 0 <= r[1] < bottom:   # visible, exact match
                near = " | ".join((e.get("name") or "")[:80] for e in els[i + 1:i + 12] if (e.get("name") or "").strip())
                return {"found": name, "details": near[:600], "verified": True}
        time.sleep(3)
    return {"found": "", "verified": False, "error": f"none of {texts} appeared within {timeout_s}s"}
