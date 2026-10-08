"""Agent runtime: seeds the registry, compiles agent graphs (with a checkpointer), runs agents and top-level runs."""
from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .. import config, events, registry
from ..models import AgentSpec, AgentTask, Report
from .sdk import ARCHETYPES, AgentContext, ApprovalDenied, PermissionDenied

log = logging.getLogger("aap.runtime")
_RESUME_RE = __import__("re").compile(r"(?i)^\s*(continue|resume|retry|try again|keep going|finish (it|the rest)|"
                                      r"do the rest|go on|carry on)\b")


def _checkpointer():
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
        return SqliteSaver(sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False))
    except Exception:  # noqa: BLE001 - package not installed: in-memory
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()


def _own_memory_notes(agent_id: str, instruction: str, k: int = 3) -> str:
    if agent_id == "main_agent":
        return ""
    try:
        import re as _re
        from ..memory import store
        words = lambda s: {w for w in _re.findall(r"[a-z0-9]{4,}", s.lower())}  # noqa: E731
        want = words(instruction.split("\n")[0])
        mine = [m for m in store.search(instruction[:400], 12, scope=agent_id)
                if m["scope"] == agent_id and len(want & words(m["text"])) >= 2][:k]   # only truly related notes
        return "\n".join(f"- {m['text'][:220]}" for m in mine)
    except Exception:  # noqa: BLE001
        return ""


def _write_own_memory(spec, task, rep) -> None:
    """After every task an agent records what it did and how it went in ITS OWN memory."""
    if spec.id == "main_agent" or spec.id.startswith("spawn_"):
        return
    try:
        from ..memory import store
        # a LESSON (outcome + what to avoid), not the past task's instructions — so it can't be mistaken for work
        topic = task.instruction.split(chr(10))[0][:90]
        note = f"Lesson ({rep.status}) from a past task about '{topic}': {rep.summary[:150]}"
        if rep.errors:
            note += f" — avoid: {str(rep.errors[0])[:110]}"
        store.add(note, "episode", tags=[rep.status], source=spec.id, scope=spec.id)
    except Exception:  # noqa: BLE001 - memory must never break a run
        pass


class Runtime:
    def __init__(self) -> None:
        self._graphs: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="run")
        self.checkpointer = _checkpointer()

    # ---- registry ------------------------------------------------------
    def bootstrap(self) -> None:
        registry.init()
        from .. import graphs  # noqa: F401  (populates ARCHETYPES)
        from . import toolfactory
        n_custom = toolfactory.load_all()   # tools the agents created for themselves
        if n_custom:
            log.info("loaded %d custom tools", n_custom)
        from ..agents.prebuilt.catalog import PREBUILT
        for spec in PREBUILT:
            existing = registry.get_agent(spec.id)
            if existing:  # keep user choices (enabled/disabled, prompt edits); refresh when the catalog changed
                keys = ("tools", "uses", "permissions", "graph", "capabilities", "config")
                if existing.version == spec.version and all(getattr(existing, k) == getattr(spec, k) for k in keys):
                    if not existing.soul and spec.soul:   # additive field introduced later: backfill only
                        existing.soul = spec.soul
                        registry.upsert_agent(existing, snapshot=False)
                    continue
                spec.status = existing.status
            registry.upsert_agent(spec)
        log.info("runtime ready: %d agents, %d archetypes", len(registry.list_agents()), len(ARCHETYPES))

    def get_spec(self, agent_id: str) -> Optional[AgentSpec]:
        return registry.get_agent(agent_id)

    def list_specs(self, only_active: bool = True) -> list[AgentSpec]:
        return registry.list_agents(include_inactive=not only_active)

    def register(self, spec: AgentSpec) -> AgentSpec:
        registry.upsert_agent(spec)
        with self._lock:  # hot reload
            for k in [k for k in self._graphs if k[0] == spec.id]:
                self._graphs.pop(k, None)
        return spec

    # ---- graphs --------------------------------------------------------
    def compiled(self, spec: AgentSpec):
        key = (spec.id, spec.version + spec.graph)
        with self._lock:
            g = self._graphs.get(key)
        if g is None:
            arch = ARCHETYPES.get(spec.graph) or ARCHETYPES["generic"]
            g = arch.build(spec).compile(checkpointer=self.checkpointer)
            with self._lock:
                self._graphs[key] = g
        return g

    def graph_json(self, agent_id: str) -> dict:
        spec = self.get_spec(agent_id)
        if not spec:
            return {"nodes": [], "edges": []}
        try:
            g = self.compiled(spec).get_graph()
            nodes = [{"id": n, "label": n} for n in g.nodes]
            edges = [{"source": e.source, "target": e.target, "conditional": bool(e.conditional)} for e in g.edges]
            return {"nodes": nodes, "edges": edges}
        except Exception as e:  # noqa: BLE001
            return {"nodes": [], "edges": [], "error": str(e)}

    def workflow_nodes(self, spec: AgentSpec) -> list[str]:
        return [n["id"] for n in self.graph_json(spec.id)["nodes"] if not n["id"].startswith("__")]

    # ---- running -------------------------------------------------------
    def run_agent(self, task: AgentTask, run: events.RunContext, spec: AgentSpec | None = None) -> Report:
        """Run one agent synchronously (called from graph nodes for delegation, or from start_* in a worker).
        `spec` = an ephemeral (unregistered) agent spawned on the fly by the Main Agent."""
        spec = spec or self.get_spec(task.agent_id)
        if spec is None:
            return Report(agent_id=task.agent_id, task_id=task.task_id, status="failed",
                          summary=f"Unknown agent {task.agent_id}", errors=["unknown agent"])
        if spec.status != "active" and spec.type != "system":
            return Report(agent_id=spec.id, task_id=task.task_id, status="failed",
                          summary=f"Agent {spec.name} is {spec.status}", errors=[f"status={spec.status}"])
        task.run_id = run.run_id
        ctx = AgentContext(spec=spec, task=task, run=run, runtime=self)
        run.emit("agent", spec.id, "running", f"{spec.name} started", task_id=task.task_id,
                 parent_agent=task.parent_agent, instruction=task.instruction[:500], depth=task.depth)
        t = time.time()
        from .. import routing
        tier_token = routing.set_tier(ctx.effort)       # every LLM call in this agent is routed by its tier
        run_token = routing.set_run(run.run_id, ctx.effort)         # ...and counted against this run's token budget
        tok0 = routing.run_tokens(run.run_id)
        own = _own_memory_notes(spec.id, task.instruction)
        if own:   # the agent's OWN memory from past tasks (separate per agent; global memory is shared)
            task.instruction += ("\n\nBACKGROUND ONLY — lessons from your memory (do NOT redo those past tasks; your "
                                 "task is ONLY the one above):\n" + own)
            ctx.task = task
            run.emit("log", spec.id, "info", f"Recalled own memory ({own.count(chr(10)) + 1} note(s))")
        try:
            run.check_cancel()
            cfg = {"configurable": {"thread_id": f"{run.run_id}:{task.task_id}", "ctx": ctx},
                   "recursion_limit": 400}   # agents bound themselves (max_steps, loop guard)
            from .. import tracing
            cfg.update(tracing.run_config(spec, task, run))   # LangSmith: run name, tags, metadata (off → empty)
            out = self.compiled(spec).invoke({"task": task.model_dump()}, config=cfg)
            rep = Report.model_validate(out.get("report") or ctx.report("Agent finished without a report",
                                                                         status="partial"))
        except (PermissionDenied, ApprovalDenied) as e:
            rep = Report.model_validate(ctx.report(f"Blocked: {e}", status="failed", errors=[str(e)]))
        except events.RunCancelled as e:
            rep = Report.model_validate(ctx.report("Cancelled", status="failed", errors=[str(e)]))
        except Exception as e:  # noqa: BLE001
            log.exception("agent %s crashed", spec.id)
            rep = Report.model_validate(ctx.report(f"{spec.name} crashed: {e}", status="failed",
                                                   errors=[f"{type(e).__name__}: {e}", traceback.format_exc()[-1500:]]))
        finally:
            routing.reset_tier(tier_token)
            routing.reset_run(run_token)
            try:   # this step's browser tab is handed back (kept open) for the next step to continue on
                from ..tools.browser import release_step
                release_step(run.run_id, task.task_id)
            except Exception:  # noqa: BLE001
                pass
        rep.duration_s = round(time.time() - t, 2)
        rep.tokens = max(0, routing.run_tokens(run.run_id) - tok0)   # real cost → better estimates next time
        _write_own_memory(spec, task, rep)
        registry.add_report(run.run_id, rep)
        run.emit("report", spec.id, "done" if rep.status != "failed" else "failed", rep.summary[:300],
                 task_id=task.task_id, report=rep.model_dump())
        run.emit("agent", spec.id, "done" if rep.status != "failed" else "failed",
                 f"{spec.name} {rep.status}", task_id=task.task_id, duration_s=rep.duration_s)
        return rep

    def _spawn(self, kind: str, request: str, agent_id: str, instruction: str, conversation_id: str = "",
               context: list[dict] | None = None, on_done=None, model: str = "", effort: int = -1,
               notify: bool = False) -> str:
        run_id = registry.new_run(kind, request)
        run = events.create(run_id, kind, conversation_id)
        run.notify = notify
        if model and model.lower() != "auto":
            from .. import routing
            routing.set_run_model(run_id, model)     # the user's pick is tried first for every call in this run

        def work():
            run.emit("run", agent_id, "running", request[:200], kind=kind, conversation_id=conversation_id)
            status, final = "failed", ""
            try:
                task = AgentTask(task_id=uuid.uuid4().hex[:8], agent_id=agent_id, instruction=instruction,
                                 context=context or [], run_id=run_id, effort=effort)   # -1 = AI decides
                rep = self.run_agent(task, run)
                final = rep.result if isinstance(rep.result, str) and rep.result else rep.summary
                status = "done" if rep.status != "failed" else "failed"
                if not run.events or run.events[-1]["type"] != "final":
                    run.emit("final", agent_id, status, "final", text=final, report=rep.model_dump())
                if on_done:
                    on_done(run, rep, final)
            except Exception as e:  # noqa: BLE001
                log.exception("run %s failed", run_id)
                final = f"Run failed: {e}"
                run.emit("final", agent_id, "failed", "final", text=final)
            finally:
                run.done = True
                try:   # free the run's browser session (memory) and loop-guard counters
                    from ..tools.browser import close_run
                    from .sdk import _CALLS
                    close_run(run_id)
                    _CALLS.pop(run_id, None)
                except Exception:  # noqa: BLE001
                    pass
                registry.finish_run(run_id, "cancelled" if run.cancelled else status, final)
                try:   # phone push: owner-started runs only (Settings → Notifications)
                    from .. import notify
                    if run.notify:
                        notify.run_finished(run_id, kind, request, "cancelled" if run.cancelled else status, final,
                                            run=run)
                except Exception:  # noqa: BLE001
                    pass
                run.emit("run", agent_id, "done" if status == "done" else "failed", "run finished", final=True)

        self._pool.submit(work)
        return run_id

    @staticmethod
    def _last_state(history: list[dict]) -> dict | None:
        from .execstate import from_report
        for m in reversed(history):
            if m.get("role") == "assistant" and m.get("run_id"):
                for r in registry.reports_for_run(m["run_id"]):
                    if r.get("agent_id") == "main_agent":
                        st = from_report(r)
                        if st:
                            return st
        return None

    def start_chat(self, message: str, conversation_id: str | None = None, kind: str = "chat",
                   on_done=None, model: str = "", effort: int = -1, notify: bool | None = None) -> tuple[str, str]:
        cid = registry.ensure_conversation(conversation_id, message)
        history = registry.get_messages(cid)[-12:]
        registry.add_message(cid, "user", message)

        def done(run, rep, final):
            registry.add_message(cid, "assistant", final, run.run_id)
            if on_done:
                on_done(run, rep, final)

        ctx_items = [{"kind": "history", "messages": history}] if history else []
        prior = self._last_state(history) if _RESUME_RE.search(message) else None
        if prior:   # "continue"/"retry"/"finish it": resume from the last task state instead of starting over
            prior = {**prior, "request": prior.get("request", "") + f"\n(Follow-up from user: {message})"}
            ctx_items.append({"kind": "exec_state", "state": prior})
            message_for_agent = (f"{message}\n\nContinue the previous task: {prior.get('request', '')[:400]}. "
                                 "Keep completed+verified work; only do what is still pending or failed.")
        else:
            message_for_agent = message
        run_id = self._spawn(kind, message, "main_agent", message_for_agent, cid, ctx_items, done,
                             model=model, effort=effort, notify=(kind == "schedule") if notify is None else notify)
        return run_id, cid

    def start_run(self, message: str, kind: str = "schedule", on_done=None, notify: bool = True) -> str:
        """A Main-Agent run WITHOUT a conversation (scheduled jobs, reminders, heartbeats): it shows up under
        Runs and on the Home feed, never in the chat list."""
        return self._spawn(kind, message, "main_agent", message, "", None, on_done, notify=notify)

    def start_agent(self, agent_id: str, instruction: str) -> str:
        return self._spawn("agent", instruction, agent_id, instruction)

    def start_builder(self, description: str, name: str = "") -> str:
        instr = description if not name else f"Agent name: {name}\n{description}"
        return self._spawn("build", description, "agent_builder", instr)

    def cancel(self, run_id: str) -> bool:
        r = events.get(run_id)
        if r and not r.done:
            r.cancelled = True
            return True
        return False


runtime = Runtime()
