"""Persist screenshots so agents can truthfully report a saved file path (not just an inline artifact)."""
import itertools
import threading

from ..clients import ServiceUnavailable, runner

_counter = itertools.count(1)
_lock = threading.Lock()
SHOT_DIR = "/shared/screenshots"


def save_png(ctx, png_b64: str, kind: str) -> dict:
    """Write the PNG to /shared/screenshots/<run_id>_<n>.png via the runner. Returns {"path"} or {"save_error"}."""
    with _lock:
        n = next(_counter)
    path = f"{SHOT_DIR}/{ctx.run.run_id}_{kind}_{n}.png"
    try:
        r = runner("POST", "/fs/write_b64", json={"path": path, "data_b64": png_b64}, timeout=30)
    except ServiceUnavailable as e:
        return {"save_error": str(e)}
    if isinstance(r, dict) and r.get("ok"):
        if path not in ctx.files_created:
            ctx.files_created.append(path)
        return {"path": path}
    return {"save_error": (r or {}).get("error", "unknown error") if isinstance(r, dict) else str(r)}
