"""Global kill hotkey Ctrl+Alt+Esc → the bridge process exits immediately."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes

log = logging.getLogger("bridge.kill")
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_ESCAPE, WM_HOTKEY = 0x1B, 0x0312


def install(on_kill=None) -> bool:
    if os.name != "nt":
        return False
    ready, ok = threading.Event(), [False]

    def loop() -> None:
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_ESCAPE):
            log.warning("could not register Ctrl+Alt+Esc (in use by another program)")
            ready.set()
            return
        ok[0] = True
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                log.warning("KILL SWITCH pressed — bridge exiting")
                if on_kill:
                    try:
                        on_kill()
                    except Exception:  # noqa: BLE001
                        pass
                os._exit(3)

    threading.Thread(target=loop, daemon=True, name="killswitch").start()
    ready.wait(3)
    return ok[0]
