"""Thin gateway HTTP client shared by the CLI and the TUI (contract: docs/CONTRACTS.md §1)."""
from __future__ import annotations

import json
from typing import Iterator

import httpx

from .paths import GATEWAY_URL


class GatewayError(RuntimeError):
    pass


class Gateway:
    def __init__(self, base_url: str = GATEWAY_URL, timeout: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout)

    def _req(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise GatewayError(f"gateway not reachable at {self.base} ({type(e).__name__}). Run `agentos start`.") from e
        if r.status_code >= 400:
            raise GatewayError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def get(self, path: str, **params):
        return self._req("GET", path, params=params or None)

    def post(self, path: str, body: dict | None = None):
        return self._req("POST", path, json=body or {})

    def put(self, path: str, body: dict):
        return self._req("PUT", path, json=body)

    # convenience
    def health(self) -> dict:
        return self.get("/api/health")

    def chat(self, message: str, conversation_id: str | None = None) -> dict:
        body = {"message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return self.post("/api/chat", body)

    def agents(self) -> list[dict]:
        return self.get("/api/agents")

    def approvals(self, status: str = "pending") -> list[dict]:
        return self.get("/api/approvals", status=status)

    def decide(self, approval_id: str, decision: str) -> dict:
        return self.post(f"/api/approvals/{approval_id}", {"decision": decision})

    def stream_events(self, run_id: str) -> Iterator[dict]:
        """Yields Event dicts from the SSE endpoint until `event: end` or a `final` event."""
        try:
            with httpx.stream("GET", f"{self.base}/api/runs/{run_id}/events", timeout=None) as r:
                if r.status_code >= 400:
                    raise GatewayError(f"events stream -> {r.status_code}")
                event_name = "message"
                for line in r.iter_lines():
                    if not line:
                        event_name = "message"
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        if event_name == "end":
                            return
                    elif line.startswith("data:") and event_name != "end":
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        try:
                            yield json.loads(payload)
                        except ValueError:
                            continue
        except httpx.HTTPError as e:
            raise GatewayError(f"event stream failed: {e}") from e


def describe_event(ev: dict) -> tuple[str, str] | None:
    """Turn an Event into (style, one-line text) for terminal rendering; None = don't show."""
    t, st, agent = ev.get("type"), ev.get("status"), ev.get("agent_id") or ""
    msg, data = ev.get("message") or "", ev.get("data") or {}
    if t == "token":
        return None
    if t == "agent":
        if st == "running":
            instr = (data.get("instruction") or msg)[:100]
            return "cyan", f"▶ {agent}  {instr}"
        return ("green" if st == "done" else "red"), f"{'✓' if st == 'done' else '✗'} {agent}  {msg[:100]}"
    if t == "node":
        return "dim", f"  · {agent}: {data.get('node') or msg}"
    if t == "tool":
        if st == "running":
            return "dim", f"    ⚙ {msg} {str(data.get('args', ''))[:80]}"
        if st == "failed":
            return "red", f"    ✗ {msg} {str(data.get('output', ''))[:100]}"
        return None
    if t == "plan":
        steps = (data.get("plan") or {}).get("steps") or []
        lines = [f"  {s.get('id')}. {s.get('agent_id')}: {s.get('instruction', '')[:80]}" for s in steps]
        return "magenta", "📋 plan\n" + "\n".join(lines) if lines else f"📋 {msg}"
    if t == "approval":
        return "yellow", f"⏸ approval needed: {msg}  (run `agentos approvals`)"
    if t == "build":
        return "blue", f"🛠 {agent}: {msg}"
    if t == "report":
        rep = data.get("report") or {}
        return "green", f"📄 report {rep.get('agent_id', agent)} [{rep.get('status', '')}]: {rep.get('summary', msg)[:120]}"
    if t == "final":
        return None
    if t in ("log", "run") and msg:
        return ("red" if st == "failed" else "dim"), f"  {agent + ': ' if agent else ''}{msg[:140]}"
    return None
