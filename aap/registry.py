"""SQLite-backed Agent Registry + run/event/report store."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from typing import Optional

from . import config
from .models import AgentSpec, Report

_local = threading.local()
_STOP = set("a an the and or of to for in on with my me i we you your is are be that this it as at by from "
            "need want please create make build new agent agents can could would should do does will using use".split())


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        _local.c = c
    return c


def init() -> None:
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL, updated_at REAL);
    CREATE TABLE IF NOT EXISTS versions(agent_id TEXT, version TEXT, data TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, kind TEXT, request TEXT, status TEXT, final TEXT,
                                    created_at REAL, finished_at REAL);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent_id TEXT,
                                       task_id TEXT, data TEXT, created_at REAL);
    CREATE INDEX IF NOT EXISTS ev_run ON events(run_id);
    CREATE INDEX IF NOT EXISTS rep_agent ON reports(agent_id);
    CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, run_id TEXT, agent_id TEXT, title TEXT, detail TEXT,
                                         status TEXT, created_at REAL, decided_at REAL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, run_id TEXT, agent_id TEXT,
                                     kind TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT,
                                        content TEXT, run_id TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS feed(id TEXT PRIMARY KEY, title TEXT, body TEXT, run_id TEXT, source TEXT,
                                    created_at REAL);
    CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, data TEXT, created_at REAL);
    CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id);
    """)
    c.commit()
    cols = {r["name"] for r in c.execute("PRAGMA table_info(conversations)")}
    if "soul" not in cols:   # additive migration: per-conversation active soul (aap/souls)
        c.execute("ALTER TABLE conversations ADD COLUMN soul TEXT DEFAULT ''")
    # runs left 'running' by a crash/restart can never finish
    c.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
    c.execute("UPDATE approvals SET status='expired' WHERE status='pending'")
    c.commit()


def _publish(channel: str, data: dict) -> None:
    from . import bus
    bus.publish(channel, data)


# ---- agents -----------------------------------------------------------------
def upsert_agent(spec: AgentSpec, snapshot: bool = True) -> AgentSpec:
    c, now = _conn(), time.time()
    data = spec.model_dump_json()
    row = c.execute("SELECT created_at FROM agents WHERE id=?", (spec.id,)).fetchone()
    if row:
        c.execute("UPDATE agents SET data=?, updated_at=? WHERE id=?", (data, now, spec.id))
    else:
        c.execute("INSERT INTO agents VALUES(?,?,?,?)", (spec.id, data, now, now))
    if snapshot and not c.execute("SELECT 1 FROM versions WHERE agent_id=? AND version=?", (spec.id, spec.version)).fetchone():
        c.execute("INSERT INTO versions VALUES(?,?,?,?)", (spec.id, spec.version, data, now))
    c.commit()
    _publish("registry", {"action": "updated" if row else "created", "agent": spec.model_dump()})
    return spec


def get_version(agent_id: str, version: str) -> Optional[AgentSpec]:
    r = _conn().execute("SELECT data FROM versions WHERE agent_id=? AND version=?", (agent_id, version)).fetchone()
    return AgentSpec.model_validate_json(r["data"]) if r else None


def get_agent(agent_id: str) -> Optional[AgentSpec]:
    r = _conn().execute("SELECT data FROM agents WHERE id=?", (agent_id,)).fetchone()
    return AgentSpec.model_validate_json(r["data"]) if r else None


def list_agents(include_inactive: bool = True) -> list[AgentSpec]:
    out = [AgentSpec.model_validate_json(r["data"]) for r in _conn().execute("SELECT data FROM agents ORDER BY created_at")]
    return out if include_inactive else [a for a in out if a.status == "active"]


def delete_agent(agent_id: str) -> None:
    c = _conn()
    spec = get_agent(agent_id)
    c.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    c.commit()
    if spec:
        _publish("registry", {"action": "deleted", "agent": spec.model_dump()})


def versions(agent_id: str) -> list[dict]:
    return [{"version": r["version"], "created_at": r["created_at"]} for r in
            _conn().execute("SELECT version, created_at FROM versions WHERE agent_id=? ORDER BY created_at", (agent_id,))]


def tokens(text: str) -> set[str]:
    """Crude stemmed tokens (first 5 chars) used for capability matching in offline mode and for ranking."""
    return {w[:5] for w in re.findall(r"[a-z0-9+#]+", text.lower().replace("_", " ")) if w not in _STOP and len(w) > 1}


def search(query: str, limit: int = 12, only_active: bool = True) -> list[tuple[float, AgentSpec]]:
    q = tokens(query)
    scored = []
    for a in list_agents(include_inactive=not only_active):
        if only_active and a.status != "active":
            continue
        cap_t = tokens(" ".join(a.capabilities))
        txt_t = tokens(f"{a.name} {a.description} {a.category}")
        score = 2.0 * len(q & cap_t) + 1.0 * len(q & txt_t)
        scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


# ---- runs / events / reports --------------------------------------------------
def new_run(kind: str, request: str) -> str:
    rid = uuid.uuid4().hex[:12]
    c = _conn()
    c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (rid, kind, request, "running", "", time.time(), None))
    c.commit()
    return rid


def finish_run(run_id: str, status: str, final: str) -> None:
    c = _conn()
    c.execute("UPDATE runs SET status=?, final=?, finished_at=? WHERE id=?", (status, final, time.time(), run_id))
    c.commit()


def list_runs(limit: int = 50) -> list[dict]:
    return [dict(r) for r in _conn().execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]


def get_run(run_id: str) -> Optional[dict]:
    r = _conn().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def add_event(run_id: str, ev: dict) -> None:
    c = _conn()
    c.execute("INSERT INTO events(run_id, ts, data) VALUES(?,?,?)", (run_id, ev["ts"], json.dumps(ev, default=str)))
    c.commit()


def get_events(run_id: str) -> list[dict]:
    return [json.loads(r["data"]) for r in _conn().execute("SELECT data FROM events WHERE run_id=? ORDER BY id", (run_id,))]


def add_report(run_id: str, rep: Report) -> None:
    c = _conn()
    c.execute("INSERT INTO reports(run_id, agent_id, task_id, data, created_at) VALUES(?,?,?,?,?)",
              (run_id, rep.agent_id, rep.task_id, rep.model_dump_json(), time.time()))
    c.commit()


def reports_for_run(run_id: str) -> list[dict]:
    return [dict(json.loads(r["data"]), run_id=run_id, created_at=r["created_at"]) for r in
            _conn().execute("SELECT data, created_at FROM reports WHERE run_id=? ORDER BY id", (run_id,))]


def reports_for_agent(agent_id: str, limit: int = 30) -> list[dict]:
    return [dict(json.loads(r["data"]), run_id=r["run_id"], created_at=r["created_at"]) for r in
            _conn().execute("SELECT data, run_id, created_at FROM reports WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                            (agent_id, limit))]


def recent_reports(limit: int = 50) -> list[dict]:
    return [dict(json.loads(r["data"]), run_id=r["run_id"], created_at=r["created_at"]) for r in
            _conn().execute("SELECT data, run_id, created_at FROM reports ORDER BY id DESC LIMIT ?", (limit,))]


# ---- approvals ----------------------------------------------------------------
def add_approval(a: dict) -> None:
    c = _conn()
    c.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)",
              (a["id"], a["run_id"], a["agent_id"], a["title"], json.dumps(a["detail"], default=str), a["status"],
               a["created_at"], None))
    c.commit()


def set_approval(aid: str, status: str) -> None:
    c = _conn()
    c.execute("UPDATE approvals SET status=?, decided_at=? WHERE id=?", (status, time.time(), aid))
    c.commit()


def list_approvals(status: str = "") -> list[dict]:
    q, args = "SELECT * FROM approvals", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    rows = _conn().execute(q + " ORDER BY created_at DESC LIMIT 200", args).fetchall()
    return [dict(dict(r), detail=json.loads(r["detail"] or "{}")) for r in rows]


# ---- audit --------------------------------------------------------------------
def add_audit(run_id: str, agent_id: str, kind: str, data: dict) -> None:
    c = _conn()
    c.execute("INSERT INTO audit(ts, run_id, agent_id, kind, data) VALUES(?,?,?,?,?)",
              (time.time(), run_id, agent_id, kind, json.dumps(data, default=str)))
    c.commit()


def list_audit(run_id: str = "", limit: int = 500) -> list[dict]:
    q, args = "SELECT * FROM audit", ()
    if run_id:
        q, args = q + " WHERE run_id=?", (run_id,)
    rows = _conn().execute(q + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
    return [dict(dict(r), data=json.loads(r["data"])) for r in rows]


# ---- conversations --------------------------------------------------------------
def ensure_conversation(cid: str | None, first_message: str) -> str:
    c, now = _conn(), time.time()
    if cid and c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
    else:
        cid = cid or uuid.uuid4().hex[:12]
        c.execute("INSERT INTO conversations(id, title, created_at, updated_at) VALUES(?,?,?,?)",
                  (cid, first_message[:60], now, now))
    c.commit()
    return cid


def get_conversation_soul(cid: str) -> str:
    r = _conn().execute("SELECT soul FROM conversations WHERE id=?", (cid,)).fetchone()
    return (r["soul"] or "") if r else ""


def set_conversation_soul(cid: str, soul: str) -> bool:
    c = _conn()
    cur = c.execute("UPDATE conversations SET soul=? WHERE id=?", (soul, cid))
    c.commit()
    return cur.rowcount > 0


def add_message(cid: str, role: str, content: str, run_id: str = "") -> None:
    c = _conn()
    c.execute("INSERT INTO messages(conversation_id, role, content, run_id, ts) VALUES(?,?,?,?,?)",
              (cid, role, content, run_id, time.time()))
    c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))
    c.commit()


def list_conversations(limit: int = 100) -> list[dict]:
    return [dict(r) for r in _conn().execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,))]


def get_messages(cid: str, limit: int = 200) -> list[dict]:
    return [dict(r) for r in _conn().execute(
        "SELECT role, content, run_id, ts FROM messages WHERE conversation_id=? ORDER BY id LIMIT ?", (cid, limit))]


def delete_conversation(cid: str) -> None:
    c = _conn()
    c.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
    c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    c.commit()


# ---- feed / schedules -------------------------------------------------------------
def add_feed(title: str, body: str, run_id: str = "", source: str = "") -> dict:
    card = {"id": uuid.uuid4().hex[:10], "title": title, "body": body, "run_id": run_id, "source": source,
            "created_at": time.time()}
    c = _conn()
    c.execute("INSERT INTO feed VALUES(?,?,?,?,?,?)", tuple(card.values()))
    c.commit()
    _publish("feed", card)
    return card


def list_feed(limit: int = 50) -> list[dict]:
    return [dict(r) for r in _conn().execute("SELECT * FROM feed ORDER BY created_at DESC LIMIT ?", (limit,))]


def save_schedule(s: dict) -> dict:
    c = _conn()
    c.execute("INSERT OR REPLACE INTO schedules VALUES(?,?,?)", (s["id"], json.dumps(s), time.time()))
    c.commit()
    return s


def list_schedules() -> list[dict]:
    return [json.loads(r["data"]) for r in _conn().execute("SELECT data FROM schedules ORDER BY created_at")]


def delete_schedule(sid: str) -> None:
    c = _conn()
    c.execute("DELETE FROM schedules WHERE id=?", (sid,))
    c.commit()


def agent_stats(agent_id: str) -> dict:
    rows = _conn().execute("SELECT data FROM reports WHERE agent_id=?", (agent_id,)).fetchall()
    st = [json.loads(r["data"])["status"] for r in rows]
    return {"runs": len(st), "success": st.count("success"), "failed": st.count("failed")}
