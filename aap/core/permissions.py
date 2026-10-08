"""Least-privilege checks applied to every tool call (in addition to the runner's own path jail)."""
from __future__ import annotations

import posixpath
from urllib.parse import urlparse

from .. import settings
from ..models import AgentSpec
from .sdk import PermissionDenied, ToolDef

_PATH_ARGS = ("path", "repo", "cwd", "path_or_text")
_WRITE_TOOLS = {"fs_write", "fs_patch", "git_commit", "git_branch"}


def _norm(p: str) -> str:
    p = p.replace("\\", "/")
    if not p.startswith("/"):
        p = "/workspace/" + p
    return posixpath.normpath(p)


def check_path(spec: AgentSpec, path: str, write: bool) -> str:
    p = _norm(path)
    if p == "/laptop" or p.startswith("/laptop/"):
        # the user's whole home folder is READ-ONLY for agents (writes/deletes stay inside /workspace and /shared)
        if (settings.get("laptop_access") or "read").lower() == "off":
            raise PermissionDenied("laptop access is turned off (Settings → laptop_access)")
        if write:
            raise PermissionDenied("/laptop is read-only: agents may read your files but write only to /workspace "
                                   "or /shared")
        return p
    for g in spec.permissions.fs:
        root = posixpath.normpath(g.path)
        if p == root or p.startswith(root.rstrip("/") + "/"):
            if write and g.mode != "rw":
                raise PermissionDenied(f"{spec.id}: {root} is read-only for this agent")
            return p
    raise PermissionDenied(f"{spec.id}: path {p} is outside its filesystem grants "
                           f"({', '.join(g.path for g in spec.permissions.fs) or 'none'})")


def check_domain(spec: AgentSpec, url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    allowed = [d.lower() for d in spec.permissions.network] + [d.lower() for d in settings.network_allowlist()]
    if "*" in allowed:
        return
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise PermissionDenied(f"{spec.id}: network access to {host or url} not allowed "
                               f"(allowed: {', '.join(allowed) or 'none'})")


def check(spec: AgentSpec, td: ToolDef, args: dict) -> None:
    p = spec.permissions
    if td.needs == "shell" and not p.shell:
        raise PermissionDenied(f"{spec.id} has no shell permission")
    if td.needs == "browser" and p.browser == "none":
        raise PermissionDenied(f"{spec.id} has no browser permission")
    if td.needs == "desktop" and not p.desktop:
        raise PermissionDenied(f"{spec.id} has no desktop permission")
    if td.needs == "network" and "url" in args:
        check_domain(spec, args["url"])
    if td.needs == "network" and "url" not in args and not (p.network or settings.network_allowlist()):
        raise PermissionDenied(f"{spec.id} has no network permission")
    for k in _PATH_ARGS:
        v = args.get(k)
        if isinstance(v, str) and v and (k != "path_or_text" or v.startswith("/")):
            check_path(spec, v, write=td.name in _WRITE_TOOLS)
    if td.name == "browser_open" and args.get("mode") == "host" and p.browser != "host":
        raise PermissionDenied(f"{spec.id} may only use the sandboxed browser")
