"""AgentOS gateway: REST + WebSocket + SSE API and the built React UI. See docs/CONTRACTS.md."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import bus, clients, config, events, registry, settings
from ..core import approvals
from ..core.runtime import runtime
from ..llm import llm
from ..memory import store as memory
from ..scheduler import jobs
from ..skills import loader as skills

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aap.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .. import tools  # noqa: F401  registers tools
    runtime.bootstrap()
    from .. import tracing
    tracing.apply()          # LangSmith env from Settings (off unless the owner added a key and the switch)
    jobs.start()
    try:
        from ..channels import base
        base.start_all()
    except Exception:  # noqa: BLE001
        log.exception("channels failed to start")
    yield


app = FastAPI(title="AgentOS", version=config.VERSION, lifespan=lifespan)
from .routes_ext import router as _ext_router  # noqa: E402  real profiles, site memory, Chrome extension
app.include_router(_ext_router)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------------------------------- health/settings
@app.get("/api/health")
def health():
    from .. import souls as souls_mod
    return {"ok": True, "version": config.VERSION, "llm": llm.health(), "services": clients.service_health(),
            "identity": souls_mod.get_identity()}


@app.get("/api/settings")
def get_settings():
    return settings.all_settings()


@app.put("/api/settings")
def put_settings(patch: dict):
    out = settings.update(patch)
    llm.reset()
    return out


@app.post("/api/settings/test")
def test_settings():
    return llm.test()


@app.get("/api/routing")
def routing_stats():
    from .. import routing
    return routing.stats() | {"last_model": llm.last_model}


@app.get("/api/models")
def models():
    try:
        return {"models": llm.list_models()}
    except Exception as e:  # noqa: BLE001
        return {"models": [], "error": f"{type(e).__name__}: {e}"[:300]}


# ------------------------------------------------------------------------------------------- agents
def _spec_out(spec, full: bool = False) -> dict:
    d = spec.model_dump()
    d["stats"] = registry.agent_stats(spec.id)
    if full:
        d["versions"] = registry.versions(spec.id)
        d["workflow_graph"] = runtime.graph_json(spec.id)
        d["workflow"] = [n["id"] for n in d["workflow_graph"]["nodes"] if not n["id"].startswith("__")]
    return d


@app.get("/api/agents")
def list_agents(type: str = "", q: str = ""):
    specs = registry.list_agents()
    if type:
        specs = [s for s in specs if s.type == type]
    if q:
        ql = q.lower()
        specs = [s for s in specs if ql in (s.name + s.description + " ".join(s.capabilities)).lower()]
    return [_spec_out(s) for s in specs]


def _get(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(404, "agent not found")
    return spec


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    return _spec_out(_get(agent_id), full=True)


@app.patch("/api/agents/{agent_id}")
def patch_agent(agent_id: str, patch: dict):
    spec = _get(agent_id)
    allowed = {"status", "system_prompt", "description", "icon", "category", "models", "config"}
    if "status" in patch and spec.type == "system" and patch["status"] != "active":
        raise HTTPException(400, "system agents cannot be disabled")
    data = spec.model_dump() | {k: v for k, v in patch.items() if k in allowed}
    if patch.get("rollback_to"):
        old = registry.get_version(agent_id, patch["rollback_to"])
        if not old:
            raise HTTPException(404, "version not found")
        data = old.model_dump()
    from ..models import AgentSpec
    return _spec_out(runtime.register(AgentSpec.model_validate(data)), full=True)


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    spec = _get(agent_id)
    if spec.type in ("prebuilt", "system"):
        raise HTTPException(400, f"{spec.type} agents can't be deleted (disable them instead)")
    registry.delete_agent(agent_id)
    return {"ok": True}


class RunBody(BaseModel):
    instruction: str


@app.post("/api/agents/{agent_id}/run")
def run_agent(agent_id: str, body: RunBody):
    _get(agent_id)
    return {"run_id": runtime.start_agent(agent_id, body.instruction)}


@app.get("/api/agents/{agent_id}/reports")
def agent_reports(agent_id: str):
    return registry.reports_for_agent(agent_id)


@app.get("/api/agents/{agent_id}/files")
def agent_files(agent_id: str):
    spec = _get(agent_id)
    files = []
    if spec.location and Path(spec.location).exists():
        for p in sorted(Path(spec.location).rglob("*")):
            if p.is_file() and p.stat().st_size < 200_000:
                files.append({"path": str(p.relative_to(spec.location)).replace("\\", "/"),
                              "content": p.read_text(encoding="utf-8", errors="replace")})
    return {"files": files}


# ------------------------------------------------------------------------------------------- chat / runs
class ChatBody(BaseModel):
    message: str
    conversation_id: str | None = None
    soul: str | None = None          # optional: set this conversation's soul before the run starts
    model: str | None = None         # optional: YOUR model choice for this message ("" / "auto" = smart router)
    effort: int | None = None        # optional: YOUR effort level 0-4 for the whole request (None = AI decides)
    source: str | None = None        # "ui" = typed by the owner in the app (only those runs send phone pushes)


@app.post("/api/chat")
def chat(body: ChatBody):
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    cid_in = body.conversation_id
    if body.soul:
        from .. import souls as souls_mod
        if souls_mod.get_soul(body.soul):
            cid_in = registry.ensure_conversation(cid_in, body.message)
            registry.set_conversation_soul(cid_in, body.soul)
    eff = -1 if body.effort is None else max(0, min(4, int(body.effort)))
    run_id, cid = runtime.start_chat(body.message, cid_in, model=body.model or "", effort=eff,
                                     notify=body.source == "ui")
    return {"run_id": run_id, "conversation_id": cid}


@app.get("/api/conversations")
def conversations():
    return registry.list_conversations()


@app.get("/api/conversations/{cid}")
def conversation(cid: str):
    from .. import souls as souls_mod
    msgs = registry.get_messages(cid)
    # a run still working has no stored assistant message yet → add a live placeholder so the UI re-attaches to it
    answered = {m.get("run_id") for m in msgs}
    for r in events.active():
        if r.conversation_id == cid and r.run_id not in answered:
            msgs.append({"role": "assistant", "content": "", "run_id": r.run_id,
                         "ts": r.events[0]["ts"] if r.events else 0, "live": True})
    return {"id": cid, "messages": msgs, "soul": souls_mod.resolve(cid),
            "soul_explicit": registry.get_conversation_soul(cid)}


class ConvSoulBody(BaseModel):
    soul: str


@app.put("/api/conversations/{cid}/soul")
def set_conversation_soul(cid: str, body: ConvSoulBody):
    from .. import souls as souls_mod
    if body.soul and not souls_mod.get_soul(body.soul):
        raise HTTPException(404, "soul not found")
    if not registry.set_conversation_soul(cid, body.soul):
        raise HTTPException(404, "conversation not found")
    return {"ok": True, "soul": souls_mod.resolve(cid)}


# ------------------------------------------------------------------------------------------- souls / workspace / identity
class SoulBody(BaseModel):
    id: str = ""
    name: str = ""
    icon: str = ""
    description: str = ""
    tone: str = ""
    best_for: list[str] = []
    body: str = ""


@app.get("/api/souls")
def souls_list():
    from .. import souls as souls_mod
    default = settings.get("default_soul") or "default"
    return [dict(s, default=s["id"] == default) for s in souls_mod.list_souls()]


@app.get("/api/souls/{soul_id}")
def souls_get(soul_id: str):
    from .. import souls as souls_mod
    s = souls_mod.get_soul(soul_id)
    if not s:
        raise HTTPException(404, "soul not found")
    return s


@app.post("/api/souls")
def souls_create(body: SoulBody):
    from .. import souls as souls_mod
    sid = body.id or souls_mod.slug(body.name)
    if souls_mod.get_soul(sid):
        raise HTTPException(409, f"soul {sid} already exists")
    try:
        return souls_mod.save_soul(sid, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/souls/{soul_id}")
def souls_update(soul_id: str, body: dict):
    from .. import souls as souls_mod
    if not souls_mod.get_soul(soul_id):
        raise HTTPException(404, "soul not found")
    if body.get("default"):
        settings.update({"default_soul": soul_id})
    data = {k: v for k, v in body.items() if k in SoulBody.model_fields and k != "id"}
    return souls_mod.save_soul(soul_id, data) if data else souls_mod.get_soul(soul_id)


@app.delete("/api/souls/{soul_id}")
def souls_delete(soul_id: str):
    from .. import souls as souls_mod
    try:
        souls_mod.delete_soul(soul_id)
    except KeyError as e:
        raise HTTPException(404, "soul not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if settings.get("default_soul") == soul_id:
        settings.update({"default_soul": "default"})
    return {"ok": True}


class ImportBody(BaseModel):
    path: str = ""


@app.post("/api/souls/import")
def souls_import(body: ImportBody):
    from .. import souls as souls_mod
    try:
        return souls_mod.import_from(body.path or None)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/workspace-files")
def ws_files():
    from .. import souls as souls_mod
    return souls_mod.list_files()


@app.get("/api/workspace-files/{name}")
def ws_file(name: str):
    from .. import souls as souls_mod
    try:
        return {"name": name.upper().removesuffix(".MD"), "content": souls_mod.get_file(name)}
    except KeyError as e:
        raise HTTPException(404, "unknown workspace file") from e


class FileBody(BaseModel):
    content: str


@app.put("/api/workspace-files/{name}")
def ws_file_put(name: str, body: FileBody):
    from .. import souls as souls_mod
    try:
        return {"name": name.upper().removesuffix(".MD"), "content": souls_mod.put_file(name, body.content)}
    except KeyError as e:
        raise HTTPException(404, "unknown workspace file") from e


class IdentityBody(BaseModel):
    name: str | None = None
    emoji: str | None = None
    vibe: str | None = None


@app.get("/api/identity")
def identity_get():
    from .. import souls as souls_mod
    return souls_mod.get_identity()


@app.put("/api/identity")
def identity_put(body: IdentityBody):
    from .. import souls as souls_mod
    ident = souls_mod.set_identity(body.name, body.emoji, body.vibe)
    bus.publish("identity", ident)
    return ident


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    registry.delete_conversation(cid)
    return {"ok": True}


class BuildBody(BaseModel):
    description: str
    name: str = ""


@app.post("/api/builder")
def builder(body: BuildBody):
    return {"run_id": runtime.start_builder(body.description, body.name)}


@app.get("/api/runs")
def runs(limit: int = 50):
    return registry.list_runs(limit)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = registry.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    live = events.get(run_id)
    evs = list(live.events) if live else registry.get_events(run_id)
    return r | {"events": evs, "reports": registry.reports_for_run(run_id)}


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str):
    return {"ok": runtime.cancel(run_id)}


@app.get("/api/tracing/status")
def tracing_status():
    from .. import tracing
    return tracing.status()


@app.post("/api/tracing/push-prompts")
def tracing_push_prompts():
    """Push every agent's system prompt to LangSmith's Prompt Hub so it can be edited there."""
    from .. import tracing
    return tracing.push_prompts()


@app.post("/api/tracing/test")
def tracing_test():
    """Check the saved LangSmith API key (Settings → Tracing → Test key)."""
    from .. import tracing
    return tracing.test()


@app.post("/api/notify/test")
def notify_test():
    from .. import notify
    return {"ok": notify.send("AgentOS test push", "Notifications work. You'll get a push when a task finishes, "
                                                   "fails, is stopped or needs you.", tags="bell", sync=True)}


@app.get("/api/modes")
def list_modes():
    """The 100 slash modes (aap/modes.py) for the chat box's `/` autocomplete."""
    from .. import modes
    return [{k: m[k] for k in ("id", "name", "group", "description", "kind")} for m in modes.catalog()]


class SteerBody(BaseModel):
    text: str


@app.post("/api/runs/{run_id}/message")
def steer_run(run_id: str, body: SteerBody):
    """Talk to a run WHILE it works ("change this part, skip that"): every agent sees it at once and the Main Agent
    re-plans the remaining steps. ok=False when the run already finished (send it as a new message instead)."""
    live = events.get(run_id)
    if not live or live.done or not body.text.strip():
        return {"ok": False}
    from ..core import runstatus
    if runstatus.STOP_RE.match(body.text):
        runtime.cancel(run_id)
        return {"ok": True, "stopped": True}
    if runstatus.is_status_question(body.text):   # a question about the task is ANSWERED; the plan is unchanged
        answer = runstatus.describe(live)
        live.emit("status", "main_agent", "info", answer, question=body.text[:300])
        return {"ok": True, "answer": answer}
    live.add_steer(body.text)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    async def gen():
        i = 0
        while True:
            live = events.get(run_id)
            evs = live.events if live else registry.get_events(run_id)
            while i < len(evs):
                yield f"data: {json.dumps(evs[i], default=str)}\n\n"
                i += 1
            if not live or live.done:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/reports")
def reports(limit: int = 50):
    return registry.recent_reports(limit)


# ------------------------------------------------------------------------------------------- approvals / audit
@app.get("/api/approvals")
def list_approvals(status: str = ""):
    return registry.list_approvals(status)


class Decision(BaseModel):
    decision: str
    remember_minutes: float = 0


@app.post("/api/approvals/{aid}")
def decide(aid: str, body: Decision):
    if body.decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be approve|deny")
    return {"ok": approvals.decide(aid, body.decision, body.remember_minutes)}


@app.get("/api/audit")
def audit(run_id: str = "", limit: int = 500):
    return registry.list_audit(run_id, limit)


# ------------------------------------------------------------------------------------------- memory / schedules / feed
class MemBody(BaseModel):
    text: str
    kind: str = "fact"
    tags: list[str] = []
    scope: str = "global"


@app.get("/api/memory")
def mem_list(q: str = "", kind: str = "", scope: str = ""):
    """scope='' all · 'global' shared memory · '<agent_id>' that agent's own memory"""
    if q:
        return [m for m in memory.search(q, 50) if not scope or m["scope"] == scope]
    return memory.list_all(kind, scope=scope)


@app.get("/api/memory/scopes")
def mem_scopes():
    from collections import Counter
    return dict(Counter(m["scope"] for m in memory.list_all(limit=5000)))


@app.post("/api/memory")
def mem_add(body: MemBody):
    return memory.add(body.text, body.kind, body.tags, source="user", scope=body.scope or "global")


@app.delete("/api/memory/{mid}")
def mem_del(mid: str):
    memory.delete(mid)
    return {"ok": True}


class SchedBody(BaseModel):
    name: str
    cron: str
    prompt: str
    enabled: bool = True
    channel: str = ""


@app.get("/api/schedules")
def sched_list():
    return registry.list_schedules()


@app.post("/api/schedules")
def sched_add(body: SchedBody):
    try:
        return jobs.create(body.name, body.cron, body.prompt, body.enabled, body.channel)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/schedules/{sid}")
def sched_update(sid: str, body: SchedBody):
    s = next((x for x in registry.list_schedules() if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, "schedule not found")
    try:
        jobs.parse(body.cron)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    s.update(body.model_dump(), next_run=jobs.next_run(body.cron))
    return registry.save_schedule(s)


@app.delete("/api/schedules/{sid}")
def sched_del(sid: str):
    registry.delete_schedule(sid)
    return {"ok": True}


@app.post("/api/schedules/{sid}/run")
def sched_run(sid: str):
    s = next((x for x in registry.list_schedules() if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, "schedule not found")
    return {"run_id": jobs.run_now(s)}


class SelftestBody(BaseModel):
    sections: list[str] = ["16"]


@app.get("/api/selftest")
def selftest_get():
    from .. import selftest
    rows = selftest.latest()
    return {"state": selftest.state(), "summary": selftest.summary(rows), "rows": rows}


@app.post("/api/selftest/run")
def selftest_run(body: SelftestBody | None = None):
    from .. import selftest
    try:
        return selftest.start((body or SelftestBody()).sections)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/questions")
def questions_list(run_id: str = ""):
    from ..core import questions
    return questions.pending(run_id)


class AnswerBody(BaseModel):
    answers: list[str]


@app.post("/api/questions/{qid}")
def questions_answer(qid: str, body: AnswerBody):
    from ..core import questions
    if not questions.answer(qid, body.answers):
        raise HTTPException(404, "question not pending (already answered or timed out)")
    return {"ok": True}


@app.get("/api/tools")
def tools_list():
    """Every tool the agents can use; custom = created by the agents themselves (Tool Builder)."""
    from ..core.sdk import TOOLS
    from ..core import toolfactory
    custom = {m["name"]: m for m in toolfactory.list_custom()}
    return [{"name": n, "description": t.description.replace("[custom] ", ""), "needs": t.needs or "",
             "params": t.params, "custom": n in custom, "created_by": custom.get(n, {}).get("created_by", ""),
             "code": custom.get(n, {}).get("code", "")} for n, t in sorted(TOOLS.items())]


_TOOLCHECK = config.DATA_DIR / "toolcheck.json"


@app.get("/api/tools/check")
def tools_check_last():
    """Last tool self-check (aap/toolcheck.py): which tools really work."""
    try:
        return json.loads(_TOOLCHECK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"counts": {}, "results": [], "at": None}


@app.post("/api/tools/check")
def tools_check_run(body: dict | None = None):
    """Run every tool with a safe sample input now (~30-60 s: web + a throwaway browser session)."""
    from .. import toolcheck
    out = toolcheck.run(include_web=(body or {}).get("web", True))
    _TOOLCHECK.write_text(json.dumps(out), encoding="utf-8")
    return out


@app.delete("/api/tools/{name}")
def tools_delete(name: str):
    from ..core import toolfactory
    if not toolfactory.delete(name):
        raise HTTPException(404, "only custom (agent-created) tools can be deleted")
    return {"ok": True}


@app.get("/api/lessons")
def lessons_list():
    """What the agents learned: worked / failed / avoid strategies per site, and user preferences."""
    from ..core import execstate
    return execstate.all_lessons()


@app.delete("/api/lessons/{index}")
def lessons_delete(index: int):
    from ..core import execstate
    if not execstate.delete_lesson(index):
        raise HTTPException(404, "lesson not found")
    return {"ok": True}


@app.get("/api/feed")
def feed():
    return registry.list_feed()


# ------------------------------------------------------------------------------------------- skills
class SkillImport(BaseModel):
    path: str = ""
    git_url: str = ""
    as_agents: bool = True


@app.get("/api/skills")
def skills_list():
    return skills.list_skills()


@app.get("/api/skills/{skill_id}")
def skills_get(skill_id: str):
    s = skills.get(skill_id)
    if not s:
        raise HTTPException(404, "skill not found")
    return {k: s[k] for k in ("id", "name", "description", "source", "path", "body", "resources")}


@app.post("/api/skills/import")
def skills_import(body: SkillImport):
    try:
        ids = skills.import_git(body.git_url) if body.git_url else skills.import_path(body.path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"import failed: {e}") from e
    if body.as_agents:
        for sid in ids:
            s = skills.get(sid)
            if s:
                runtime.register(skills.as_agent_spec(s))
    return {"imported": ids}


# ------------------------------------------------------------------------------------------- browser / mounts
@app.get("/api/browser/status")
def browser_status():
    out = {"mode": settings.get("browser_default_mode") or "sandbox",
           "active_profile": settings.get("browser_profile") or "default",
           "vnc_url": config.VNC_PUBLIC_URL, "profiles": ["default"], "url": ""}
    try:
        p = clients.browser("GET", "/profiles", timeout=3)
        out["profiles"] = p.get("profiles", ["default"]) if isinstance(p, dict) else ["default"]
        out["available"] = True
    except clients.ServiceUnavailable as e:
        out["available"], out["error"] = False, str(e)
    from .. import vmbrowser
    out["vm"] = vmbrowser.status()
    return out


@app.post("/api/browser/profile")
def browser_profile(body: dict):
    """Persist the default browser (sandbox | host = your Chrome) and profile used by browser agents."""
    patch = {}
    if body.get("mode") in ("sandbox", "host", "extension", "vm"):
        patch["browser_default_mode"] = body["mode"]
    if body.get("profile"):
        import re as _re
        patch["browser_profile"] = _re.sub(r"[^A-Za-z0-9_-]", "-", str(body["profile"]))[:40] or "default"
    if patch:
        settings.update(patch)
    return browser_status()


class VmLoginBody(BaseModel):
    profile: str
    url: str = ""
    extension: bool = True   # also load the AgentOS extension (pre-paired) so the profile appears on the Browser page


@app.post("/api/browser/vm-login")
def browser_vm_login(body: VmLoginBody, request: Request):
    """Open `profile` at `url` in a PLAIN Chrome on the VM screen (no automation attached — Google and other sites
    refuse sign-in in automated browsers) so the owner logs in ONCE, watching via noVNC. The login is kept in the
    VM profile; afterwards agents use that profile there — never the owner's own screen (M7)."""
    import re as _re
    from .. import vmbrowser
    prof = _re.sub(r"[^A-Za-z0-9_-]", "-", body.profile.strip())[:40]
    if not prof or prof.lower() == "default":
        raise HTTPException(400, "pick a profile name (e.g. asdf)")
    url = body.url if not body.url or _re.match(r"^https?://", body.url) else "https://" + body.url
    try:
        payload: dict = {"profile": prof, "urls": [url] if url else []}
        if body.extension:   # the VM's Chrome reaches THIS gateway on localhost (mirrored networking)
            from .. import extension
            payload.update(extension_token=extension.token(),
                           extension_url=f"ws://127.0.0.1:{request.url.port or 7860}/ws/extension")
        r = vmbrowser.call("POST", "/manual", json=payload)
    except clients.ServiceUnavailable as e:
        raise HTTPException(503, f"VM browser unavailable: {e}") from e
    if isinstance(r, dict) and r.get("error"):
        raise HTTPException(502, r["error"])
    vmbrowser.profiles(refresh=True)
    return {"session_id": prof, "profile": prof, "view_url": vmbrowser.view_url()}


@app.post("/api/browser/vm-login/{profile}/done")
def browser_vm_login_done(profile: str):
    """The owner finished logging in: close the plain Chrome (the login stays in the profile for the agents)."""
    from .. import vmbrowser
    try:
        vmbrowser.call("POST", "/manual/close", json={"profile": profile})
    except clients.ServiceUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "vm": vmbrowser.status()}


@app.post("/api/browser/takeover")
def browser_takeover(body: dict):
    try:
        return clients.browser("POST", "/takeover", json={"on": bool(body.get("on"))})
    except clients.ServiceUnavailable as e:
        raise HTTPException(503, str(e)) from e


@app.get("/api/mounts")
def mounts():
    root = Path("/mounts")
    out = [{"name": "shared", "container_path": "/shared", "mode": "rw"},
           {"name": "workspace", "container_path": "/workspace", "mode": "rw"}]
    if root.exists():
        out += [{"name": p.name, "container_path": str(p), "mode": "ro" if not _writable(p) else "rw"}
                for p in root.iterdir() if p.is_dir()]
    return out


def _writable(p: Path) -> bool:
    import os
    return os.access(p, os.W_OK)


# ------------------------------------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q = bus.subscribe_queue()
    sub: set[str] = set()

    async def reader():
        while True:
            msg = await socket.receive_json()
            if isinstance(msg, dict) and msg.get("subscribe"):
                sub.add(msg["subscribe"])

    rt = asyncio.create_task(reader())
    try:
        while True:
            msg = await q.get()
            if msg["channel"] == "run" and sub and msg["data"].get("run_id") not in sub:
                continue
            await socket.send_text(json.dumps(msg, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.cancel()
        bus.unsubscribe_queue(q)


# ------------------------------------------------------------------------------------------- web UI
if config.WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.WEB_DIST / path
        if path and f.is_file() and config.WEB_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(config.WEB_DIST / "index.html")
