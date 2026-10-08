"""Real-desktop control (Windows): screenshot (mss), UI Automation tree (pywinauto), actions (pywinauto/pyautogui).
All heavy imports are lazy so the bridge (and its tests) load without a GUI session."""
from __future__ import annotations

import io
import subprocess
import threading
import time
from typing import Any

_refs: dict[str, Any] = {}
_lock = threading.Lock()
MAX_ELEMENTS = 400


def screenshot_png() -> bytes:
    import mss
    import mss.tools
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])  # all monitors
        return mss.tools.to_png(shot.rgb, shot.size)


def _rect(el) -> list[int]:
    try:
        r = el.rectangle()
        return [r.left, r.top, r.right, r.bottom]
    except Exception:  # noqa: BLE001
        return []


def _inner_names(w) -> str:
    """Names of a window's direct children — Chrome puts the PROFILE there ('… - Google Chrome - asdf')."""
    try:
        return " | ".join((ch.element_info.name or "") for ch in w.children()[:6])
    except Exception:  # noqa: BLE001
        return ""


_A11Y_DONE: set[int] = set()


def _enable_web_a11y(win) -> None:
    """Chromium builds the web page's accessibility tree only when an assistive client asks for it. Asking each
    render widget for its MSAA object (what screen readers do) turns it on, so the UIA tree then contains the page
    (buttons, editor, results) instead of only the browser frame. Harmless for non-browser windows."""
    try:
        import ctypes
        import ctypes.wintypes as W
        import comtypes
        hwnd = int(win.handle or 0)
        if not hwnd:
            return
        user32, oleacc = ctypes.windll.user32, ctypes.windll.oleacc
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)

        def cb(h, _l):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, buf, 64)
            if buf.value == "Chrome_RenderWidgetHostHWND":   # re-ask every time: a reload/new renderer resets it
                found.append(int(h))
            return True
        user32.EnumChildWindows(hwnd, proc(cb), 0)
        iid = comtypes.GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")   # IID_IAccessible
        for h in found:
            p = ctypes.c_void_p()
            if oleacc.AccessibleObjectFromWindow(h, 0xFFFFFFFC, ctypes.byref(iid), ctypes.byref(p)) == 0:
                _A11Y_DONE.add(h)
        if found:
            time.sleep(0.4)   # give the renderer a moment to build the tree
    except Exception:  # noqa: BLE001
        pass


def tree(window: str = "", contains: str = "") -> dict:
    """Top-level windows + a flattened element list (with refs) for the named or foreground window.
    `contains`: among windows matching `window`, pick the one whose inner pane names contain it (e.g. '- asdf')."""
    from pywinauto import Desktop

    desk = Desktop(backend="uia")
    wins = []
    target = None
    for w in desk.windows():
        try:
            title = w.window_text()
        except Exception:  # noqa: BLE001
            continue
        if not title:
            continue
        info = {"title": title, "rect": _rect(w)}
        try:
            info["active"] = bool(w.has_keyboard_focus() or w.is_active())
        except Exception:  # noqa: BLE001
            info["active"] = False
        wins.append(info)
        if window and window.lower() in title.lower() and target is None:
            if contains:
                inner = _inner_names(w)
                info["inner"] = inner[:200]
                if contains.lower() not in (title + " " + inner).lower():
                    continue          # same title, other profile: keep looking
            target = w
        elif not window and info["active"] and target is None:
            target = w
    elements = []
    if target is not None:
        _enable_web_a11y(target)   # Chrome/Edge only expose page content after a screen-reader-style request
    with _lock:
        _refs.clear()
        if target is not None:
            _refs["w0"] = target
            for i, el in enumerate(target.descendants()):
                if i >= MAX_ELEMENTS:
                    break
                try:
                    ei = el.element_info
                    name, ctype = ei.name or "", ei.control_type or ""
                except Exception:  # noqa: BLE001
                    continue
                if not name and ctype not in ("Edit", "Button", "ComboBox", "CheckBox"):
                    continue
                ref = f"e{i}"
                _refs[ref] = el
                item = {"ref": ref, "name": name[:120], "control_type": ctype, "rect": _rect(el)}
                if ctype in ("Edit", "Document", "Hyperlink") and len(elements) < MAX_ELEMENTS:
                    try:   # text fields / code editors: their CONTENT (to verify typed or pasted text)
                        v = el.iface_value.CurrentValue
                        if v:
                            item["value"] = str(v)[:3000]
                    except Exception:  # noqa: BLE001
                        pass
                elements.append(item)
    return {"windows": wins, "target": target.window_text() if target is not None else None, "elements": elements}


def _hwnd(el) -> int:
    try:
        return int(el.handle or 0)
    except Exception:  # noqa: BLE001
        try:
            return int(el.element_info.handle or 0)
        except Exception:  # noqa: BLE001
            return 0


def user_idle_seconds() -> float:
    """Seconds since the owner's last mouse/keyboard input (GetLastInputInfo)."""
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)


# Agent-generated input also resets the idle timer — remember when WE last acted so it doesn't count as the owner.
_last_agent_input = [0.0]


def owner_active(threshold: float = 20.0) -> bool:
    idle = user_idle_seconds()
    since_agent = time.time() - _last_agent_input[0]
    return idle < threshold and since_agent > idle + 1.0


def is_foreground(el) -> bool:
    import win32gui
    h = _hwnd(el)
    return bool(h) and win32gui.GetForegroundWindow() == h


def force_foreground(el, tries: int = 4) -> bool:
    """Bring a window to the front even when another app owns the foreground (Windows' foreground lock):
    restore it, attach our input to the foreground thread, tap Alt, SetForegroundWindow — then VERIFY."""
    import ctypes
    import win32con
    import win32gui
    import win32process
    h = _hwnd(el)
    if not h:
        el.set_focus()
        return True
    user32 = ctypes.windll.user32
    for _ in range(tries):
        if win32gui.GetForegroundWindow() == h:
            return True
        try:
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            fg = win32gui.GetForegroundWindow()
            fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            me = ctypes.windll.kernel32.GetCurrentThreadId()
            attached = bool(fg_thread) and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
            user32.keybd_event(0x12, 0, 0, 0)          # Alt down/up unlocks SetForegroundWindow
            user32.keybd_event(0x12, 0, 2, 0)
            win32gui.ShowWindow(h, win32con.SW_SHOW)
            win32gui.BringWindowToTop(h)
            win32gui.SetForegroundWindow(h)
            if attached:
                user32.AttachThreadInput(me, fg_thread, False)
        except Exception:  # noqa: BLE001
            try:
                el.set_focus()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.4)
    return win32gui.GetForegroundWindow() == h


def _el(ref: str):
    with _lock:
        el = _refs.get(ref)
    if el is None:
        raise KeyError(f"unknown ref {ref!r} — call /desktop/tree first")
    return el


def act(action: str, ref: str | None = None, x: int | None = None, y: int | None = None, text: str | None = None,
        keys: list[str] | str | None = None, app: str | None = None) -> dict:
    import pyautogui
    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort

    # SAFETY 1: never take over the screen while the owner is using the computer
    if action in ("click", "double_click", "type", "hotkey", "paste", "focus") and owner_active():
        raise RuntimeError(f"owner is using the computer (last input {user_idle_seconds():.0f}s ago) — not taking "
                           "over the mouse/keyboard; try again when idle")
    _last_agent_input[0] = time.time()
    # SAFETY 2: input goes to whatever window is in front — make sure that is the window the agent observed
    if action in ("click", "double_click", "type", "hotkey", "paste") and not ref:
        with _lock:
            target = _refs.get("w0")
        if target is not None and not is_foreground(target) and not force_foreground(target):
            raise RuntimeError("target window is not in the foreground (the user may be using another app) — "
                               "refusing to send input to the wrong window")

    if action in ("click", "double_click"):
        if ref:
            el = _el(ref)
            el.click_input(double=action == "double_click")
        elif x is not None and y is not None:
            (pyautogui.doubleClick if action == "double_click" else pyautogui.click)(x, y)
        else:
            raise ValueError("click needs ref or x,y")
    elif action == "type":
        if ref:
            el = _el(ref)
            el.set_focus()
        pyautogui.write(text or "", interval=0.01)
    elif action == "hotkey":
        seq = keys.split("+") if isinstance(keys, str) else list(keys or [])
        if not seq:
            raise ValueError("hotkey needs keys")
        pyautogui.hotkey(*[k.strip().lower() for k in seq])
    elif action == "paste":
        # exact text into the focused field via the clipboard (keeps code formatting; editors don't auto-indent it)
        set_clipboard(text or "")
        pyautogui.hotkey("ctrl", "v")
    elif action == "focus":
        if not force_foreground(_el(ref or "w0")):
            raise RuntimeError("could not bring the window to the foreground (another app is holding focus)")
        return {"ok": True, "foreground": True}
    elif action == "launch":
        if not app:
            raise ValueError("launch needs app")
        subprocess.Popen(app if app.lower().endswith(".exe") else ["cmd", "/c", "start", "", app], shell=False)
    else:
        raise ValueError(f"unknown action {action}")
    return {"ok": True}


def set_clipboard(text: str) -> None:
    import win32clipboard  # pywin32 (installed with pywinauto)
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def png_bytes_io(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)
