"""Filesystem tools — executed by the runner container (path-jailed there and checked by permissions here)."""
from ..clients import ServiceUnavailable, runner
from ..core.sdk import tool


def _safe(fn):
    def wrap(ctx, **kw):
        try:
            return fn(ctx, **kw)
        except ServiceUnavailable as e:
            return {"error": str(e)}
    return wrap


@tool("fs_list", "List files/dirs under a path (container path like /workspace/project).",
      {"path": {"type": "string"}, "depth": {"type": "integer", "default": 2}})
@_safe
def fs_list(ctx, path: str, depth: int = 2):
    return runner("GET", "/fs/list", params={"path": path, "depth": depth})


@tool("fs_read", "Read a text file. Optional 1-based line range start/end (0 = whole file).",
      {"path": {"type": "string"}, "start": {"type": "integer", "default": 0}, "end": {"type": "integer", "default": 0}})
@_safe
def fs_read(ctx, path: str, start: int = 0, end: int = 0):
    return runner("GET", "/fs/read", params={"path": path, "start": start, "end": end})


@tool("fs_write", "Create or overwrite a text file with the full content.",
      {"path": {"type": "string"}, "content": {"type": "string"}})
@_safe
def fs_write(ctx, path: str, content: str):
    r = runner("POST", "/fs/write", json={"path": path, "content": content})
    if isinstance(r, dict) and r.get("ok"):
        (ctx.files_created if r.get("created") else ctx.files_modified).append(path)
    return r


@tool("fs_patch", "Edit a file by replacing an exact snippet `old` with `new` (old must match exactly once).",
      {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}})
@_safe
def fs_patch(ctx, path: str, old: str, new: str):
    r = runner("POST", "/fs/patch", json={"path": path, "old": old, "new": new, "count": 1})
    if isinstance(r, dict) and r.get("ok") and path not in ctx.files_modified:
        ctx.files_modified.append(path)
    return r
