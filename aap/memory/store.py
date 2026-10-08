"""Long-term memory: SQLite FTS5 (+ optional embeddings from the endpoint), mirrored to readable markdown files."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid

from .. import bus, config

_lock = threading.Lock()
_local = threading.local()
KINDS = ("fact", "episode", "project")


def _c() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.DATA_DIR / "memory.db", timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mem(id TEXT PRIMARY KEY, kind TEXT, text TEXT, tags TEXT, source TEXT,
                                       created_at REAL, embedding TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(id UNINDEXED, text, tags);
        """)
        cols = {r[1] for r in c.execute("PRAGMA table_info(mem)")}
        if "scope" not in cols:   # 'global' (shared by every agent) or an agent id (that agent's own memory)
            c.execute("ALTER TABLE mem ADD COLUMN scope TEXT DEFAULT 'global'")
            c.commit()
        _local.c = c
    return c


def _row(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "text": r["text"], "tags": json.loads(r["tags"] or "[]"),
            "source": r["source"], "created_at": r["created_at"],
            "scope": (r["scope"] if "scope" in r.keys() else None) or "global"}


def add(text: str, kind: str = "fact", tags: list[str] | None = None, source: str = "user",
        scope: str = "global") -> dict:
    from ..llm import llm
    text = text.strip()
    kind = kind if kind in KINDS else "fact"
    c = _c()
    dup = c.execute("SELECT * FROM mem WHERE text=? AND IFNULL(scope,'global')=?", (text, scope or "global")).fetchone()
    if dup:
        return _row(dup)
    vec = llm.embed([text])
    m = {"id": uuid.uuid4().hex[:10], "kind": kind, "text": text, "tags": tags or [], "source": source,
         "created_at": time.time(), "scope": scope or "global"}
    with _lock:
        c.execute("INSERT INTO mem(id, kind, text, tags, source, created_at, embedding, scope) VALUES(?,?,?,?,?,?,?,?)",
                  (m["id"], kind, text, json.dumps(m["tags"]), source, m["created_at"],
                   json.dumps(vec[0]) if vec else None, m["scope"]))
        c.execute("INSERT INTO mem_fts(id, text, tags) VALUES(?,?,?)", (m["id"], text, " ".join(m["tags"])))
        c.commit()
    _mirror()
    bus.publish("memory", m)
    return m


def delete(mid: str) -> None:
    c = _c()
    with _lock:
        c.execute("DELETE FROM mem WHERE id=?", (mid,))
        c.execute("DELETE FROM mem_fts WHERE id=?", (mid,))
        c.commit()
    _mirror()


def list_all(kind: str = "", limit: int = 500, scope: str = "") -> list[dict]:
    q, conds, a = "SELECT * FROM mem", [], ()
    if kind:
        conds.append("kind=?"); a += (kind,)
    if scope:
        conds.append("IFNULL(scope,'global')=?"); a += (scope,)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return [_row(r) for r in _c().execute(q + " ORDER BY created_at DESC LIMIT ?", a + (limit,))]


def _fts_query(q: str) -> str:
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in q.lower()).split() if len(w) > 2]
    return " OR ".join(f'"{w}"*' for w in words[:12])


def search(query: str, k: int = 5, scope: str | None = None) -> list[dict]:
    """scope=None: everything; scope='<agent_id>': that agent's own memory + the global memory."""
    from ..llm import llm
    c = _c()
    hits: dict[str, float] = {}
    fq = _fts_query(query)
    if fq:
        for i, r in enumerate(c.execute("SELECT id FROM mem_fts WHERE mem_fts MATCH ? ORDER BY rank LIMIT 20", (fq,))):
            hits[r["id"]] = hits.get(r["id"], 0) + 1.0 / (1 + i)
    qv = llm.embed([query])
    if qv:
        for r in c.execute("SELECT id, embedding FROM mem WHERE embedding IS NOT NULL"):
            v = json.loads(r["embedding"])
            dot = sum(a * b for a, b in zip(qv[0], v))
            n = math.sqrt(sum(a * a for a in qv[0])) * math.sqrt(sum(b * b for b in v)) or 1
            if dot / n > 0.3:
                hits[r["id"]] = hits.get(r["id"], 0) + dot / n
    ids = sorted(hits, key=lambda x: -hits[x])
    rows = {r["id"]: _row(r) for r in c.execute(
        f"SELECT * FROM mem WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out = [rows[i] for i in ids if i in rows and (scope is None or rows[i]["scope"] in ("global", scope))]
    return out[:k]


def _mirror() -> None:
    """Readable markdown copy (OpenClaw-style MEMORY.md) — informational, the DB is the source of truth."""
    try:
        for kind in KINDS:
            items = list_all(kind)
            body = f"# {kind.title()} memories\n\n" + "\n".join(
                f"- {m['text']}" + (f"  _({', '.join(m['tags'])})_" if m["tags"] else "") for m in items)
            (config.MEMORY_MD_DIR / f"{kind}s.md").write_text(body + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
