"""HTTP clients for the sandboxed services (runner, browser) and the optional Windows host bridge."""
from __future__ import annotations

import os
from typing import Any

import httpx

from . import config, settings


class ServiceUnavailable(Exception):
    pass


def _call(base: str, method: str, path: str, timeout: float = 130, headers: dict | None = None, **kw) -> Any:
    if not base:
        raise ServiceUnavailable("sandbox service not configured — run AgentOS with Docker (`agentos start`)")
    try:
        r = httpx.request(method, base.rstrip("/") + path, timeout=timeout, headers=headers, **kw)
    except httpx.HTTPError as e:
        raise ServiceUnavailable(f"{base} unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:  # noqa: BLE001
            detail = r.text
        return {"error": f"HTTP {r.status_code}: {detail}"}
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    return r.json()


def runner(method: str, path: str, **kw) -> Any:
    return _call(config.RUNNER_URL, method, path, **kw)


def browser(method: str, path: str, **kw) -> Any:
    return _call(config.BROWSER_URL, method, path, **kw)


def bridge(method: str, path: str, approval_id: str = "", **kw) -> Any:
    h = {"X-Bridge-Token": settings.get("bridge_token") or ""}
    if approval_id:
        h["X-Approval-Id"] = approval_id
    kw.setdefault("timeout", 90)
    return _call(config.BRIDGE_URL, method, path, headers=h, **kw)


def egress_client(timeout: float = 30) -> httpx.Client:
    proxy = os.environ.get("AAP_EGRESS_PROXY") or None   # gateway web tools also go through the egress proxy
    return httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (AgentOS research agent)"})


_health_cache: tuple[float, dict] = (0.0, {})


def service_health() -> dict:
    """Probe services in parallel with short timeouts; cached for 5 s (the UI polls this often).
    Refused connections on Windows take ~1-2 s each, so sequential probing made /api/health slow."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    global _health_cache
    if time.time() - _health_cache[0] < 5:
        return dict(_health_cache[1])

    def probe(fn) -> bool:
        try:
            r = fn("GET", "/health", timeout=0.8)   # local services answer in ms; refused ports hang ~2 s
            return bool(isinstance(r, dict) and r.get("ok"))
        except ServiceUnavailable:
            return False

    with ThreadPoolExecutor(3) as ex:
        futs = {n: ex.submit(probe, fn) for n, fn in (("runner", runner), ("browser", browser), ("bridge", bridge))}
        out = {n: f.result() for n, f in futs.items()}
    _health_cache = (time.time(), out)
    return out
