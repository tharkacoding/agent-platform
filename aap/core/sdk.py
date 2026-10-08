"""Agent SDK — the contract every agent graph, tool and archetype follows.

How an agent runs
-----------------
* An agent is an AgentSpec (manifest) + a graph *archetype* (aap/graphs/*.py) or a custom graph.py.
* Archetypes register a builder:   @archetype("coding", nodes=[...])  def build(spec) -> StateGraph (uncompiled)
* runtime.py compiles it with a checkpointer and invokes it with
      graph.invoke({"task": AgentTask.model_dump()}, config={"configurable": {"thread_id": ..., "ctx": AgentContext}})
* Nodes get the context via `get_ctx(config)` and MUST put a Report dict into state["report"] before END.

Tools
-----
* Tools are plain functions registered with @tool(...). They receive the AgentContext first.
* ctx.call_tool(name, **args) enforces the agent's allow-list + Permissions, emits a `tool` event and audits it.
* ctx.lc_tools() returns LangChain StructuredTools (for ChatOpenAI.bind_tools in ReAct loops).
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from ..models import AgentSpec, AgentTask, Report

if TYPE_CHECKING:  # pragma: no cover
    from ..events import RunContext

# ---------------------------------------------------------------------------------------------- tools
@dataclass
class ToolDef:
    name: str
    description: str
    params: dict                       # JSON schema "properties" dict; required = all keys without "default"
    fn: Callable[..., Any]             # fn(ctx, **args) -> JSON-serialisable
    needs: str = ""                    # permission gate: "" | "shell" | "browser" | "desktop" | "network" | "fs_write"
    risky: Callable[[dict], Optional[str]] | None = None   # returns a reason string if this call needs approval


TOOLS: dict[str, ToolDef] = {}
# small read-only helpers EVERY agent may use (incl. agents the Builder makes later), so nobody improvises them with
# throwaway code (TODO N3/N4: e.g. reading Chrome's Local State to find profile "asdf")
UNIVERSAL_TOOLS = ("now", "calc", "chrome_profiles", "chrome_profile_path", "chrome_extension_profiles", "find_command", "known_commands", "fs_find")
_CALLS: dict[str, dict[str, tuple[int, int]]] = {}   # run_id -> call signature -> (ok, failed) for the loop guard
_OBSERVE_TOOLS = {"browser_snapshot", "browser_screenshot", "desktop_tree", "desktop_screenshot", "git_status", "git_diff",
                  "browser_open"}   # browser_open reuses the run's session: every browser agent calls it once


def tool(name: str, description: str, params: dict, needs: str = "",
         risky: Callable[[dict], Optional[str]] | None = None):
    def deco(fn):
        TOOLS[name] = ToolDef(name, description, params, fn, needs, risky)
        return fn
    return deco


# ---------------------------------------------------------------------------------------------- archetypes
@dataclass
class Archetype:
    key: str
    build: Callable[[AgentSpec], Any]  # returns an uncompiled langgraph StateGraph
    description: str = ""


ARCHETYPES: dict[str, Archetype] = {}


def archetype(key: str, description: str = ""):
    def deco(fn):
        ARCHETYPES[key] = Archetype(key, fn, description)
        return fn
    return deco


# ---------------------------------------------------------------------------------------------- context
class ApprovalDenied(Exception):
    pass


class PermissionDenied(Exception):
    pass


@dataclass
class AgentContext:
    """Per-invocation context passed to every node through config['configurable']['ctx']."""
    spec: AgentSpec
    task: AgentTask
    run: "RunContext"
    runtime: Any                                        # aap.core.runtime.Runtime
    started: float = field(default_factory=time.time)
    tool_log: list[dict] = field(default_factory=list)
    delegated: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)

    # -- events -----------------------------------------------------------
    def emit(self, message: str, status: str = "info", type: str = "log", **data) -> None:
        self.run.emit(type, self.spec.id, status, message, task_id=self.task.task_id, **data)

    def node(self, name: str, message: str = "", **data) -> None:
        """Call at the start of every graph node so the UI can draw live progress."""
        self.run.check_cancel()   # Stop works between ANY two nodes, not only at tool calls (16.6: ran on for minutes)
        self.run.emit("node", self.spec.id, "running", message or name, node=name, task_id=self.task.task_id, **data)

    # -- effort / token budget -------------------------------------------------
    @property
    def effort(self) -> int:
        from .effort import clamp, heuristic
        return clamp(self.task.effort) if self.task.effort >= 0 else heuristic(self.task.instruction)

    @property
    def budget(self) -> dict:
        """Tier profile: model_role, max_tokens, react_steps, fix_attempts, review_loops, critic, ... (effort.py)."""
        from .effort import profile
        return profile(self.effort)

    def trim(self, text: str) -> str:
        from .effort import trim
        return trim(text, self.effort)

    # -- llm --------------------------------------------------------------
    def llm(self):
        from ..llm import llm
        return llm

    def role_model(self, role: str = "worker"):
        """LangChain ChatOpenAI for tool calling, honouring spec.models overrides."""
        from ..llm import chat_model
        return chat_model(role, override=self.spec.models.get(role))

    # -- tools ------------------------------------------------------------
    def call_tool(self, name: str, /, **args) -> Any:   # positional-only: tools may have a `name` param
        from . import permissions
        self.run.check_cancel()
        td = TOOLS.get(name)
        if td is None:
            raise PermissionDenied(f"unknown tool {name}")
        if name not in self.spec.tools and not _is_custom(name) and name not in UNIVERSAL_TOOLS:   # custom: shared
            raise PermissionDenied(f"agent {self.spec.id} is not allowed to use tool {name}")
        permissions.check(self.spec, td, args)                      # raises PermissionDenied
        from . import approvals
        rule = approvals.policy(self.spec.id, name, args)           # user autonomy rules
        if rule == "never":
            raise PermissionDenied(f"blocked by your autonomy rule: {name}")
        reason = td.risky(args) if td.risky else None
        if rule == "ask" and not reason:
            reason = "your autonomy rules ask before this action"
        if rule == "allow" and reason and "destructive" not in reason:
            reason = None                                           # pre-approved (never overrides destructive)
        if reason:
            self.approve(f"{name}: {reason}", {"tool": name, "args": args})  # raises ApprovalDenied
        t = time.time()
        # loop guard (whole run, all agents): an identical call that already failed twice, or the same call made
        # many times (screenshot/wait/snapshot loops), is refused so the agent must change strategy
        import json as _json
        key = name + ":" + _json.dumps(args, sort_keys=True, default=str)[:400]
        calls = _CALLS.setdefault(self.run.run_id, {})
        n_ok, n_err = calls.get(key, (0, 0))
        # observations are SUPPOSED to repeat (one after every action); the browser graph detects pages that don't
        # change. For them only repeated failures count. Everything else: identical call ≤4x.
        observe_tool = name in _OBSERVE_TOOLS
        if n_err >= 2 or (not observe_tool and n_ok + n_err >= 4):
            msg = (f"loop guard: `{name}` with these exact arguments was already called {n_ok + n_err}x "
                   f"({n_err} failed). Do something DIFFERENT (other arguments, another tool, or report the problem).")
            self.run.emit("tool", self.spec.id, "failed", name, task_id=self.task.task_id, loop_guard=True,
                          output=msg)
            return {"error": msg, "loop_guard": True}
        self.run.emit("tool", self.spec.id, "running", name, args=_short_args(args), task_id=self.task.task_id)
        try:
            out = td.fn(self, **args)
            ok = not (isinstance(out, dict) and out.get("error"))
        except Exception as e:  # noqa: BLE001
            out, ok = {"error": f"{type(e).__name__}: {e}"}, False
        calls[key] = (n_ok + (1 if ok else 0), n_err + (0 if ok else 1))
        if len(_CALLS) > 200:
            _CALLS.pop(next(iter(_CALLS)))
        rec = {"tool": name, "args": _short_args(args), "ok": ok, "duration_s": round(time.time() - t, 2),
               "output": _short(out, 600)}
        self.tool_log.append(rec)
        self.run.emit("tool", self.spec.id, "done" if ok else "failed", name, task_id=self.task.task_id, **rec)
        seen = getattr(self, "_steer_seen", 0)
        if len(getattr(self.run, "steer", []) or []) > seen:   # the user sent new orders: tool loops see them now
            upd = self.run.steer_block(seen)
            self._steer_seen = len(self.run.steer)
            if isinstance(out, dict):
                out = {**out, "USER_UPDATE": upd}
            elif isinstance(out, str):
                out = out + "\n\n" + upd
            else:
                out = {"output": out, "USER_UPDATE": upd}
        from . import audit
        audit.log(self.run.run_id, self.spec.id, "tool", rec)
        return out

    def lc_tools(self, names: list[str] | None = None):
        from langchain_core.tools import StructuredTool
        out = []
        for n in names or (self.spec.tools + [k for k in TOOLS if _is_custom(k) and k not in self.spec.tools]):
            td = TOOLS.get(n)
            if not td:
                continue
            schema = {"type": "object", "properties": td.params,
                      "required": [k for k, v in td.params.items() if "default" not in v]}
            out.append(StructuredTool.from_function(
                func=(lambda _n=n, **kw: self.call_tool(_n, **kw)), name=n, description=td.description,
                args_schema=schema))
        return out

    # -- delegation --------------------------------------------------------
    def delegate(self, agent_id: str, instruction: str, context: list[dict] | None = None,
                 effort: int | None = None) -> Report:
        from .. import config, routing
        if routing.over_budget(self.run.run_id, self.effort):
            cap = routing.user_cap(self.run.run_id)
            why = f"your {cap:,}-token limit is used up" if cap else "token budget for this request reached"
            self.emit(f"Not starting {agent_id}: {why}", status="info")
            return Report(agent_id=agent_id, task_id="budget", status="failed",
                          summary=f"Skipped: {why}", errors=["token limit"])
        if agent_id not in self.spec.uses and self.spec.type != "system":
            raise PermissionDenied(f"{self.spec.id} may not delegate to {agent_id} (not in `uses`)")
        if self.task.depth + 1 > config.MAX_DELEGATION_DEPTH:
            raise PermissionDenied("max delegation depth reached")
        self.delegated.append(agent_id)
        sub = AgentTask(task_id=uuid.uuid4().hex[:8], agent_id=agent_id, instruction=instruction,
                        context=context or [], run_id=self.run.run_id, parent_agent=self.spec.id,
                        depth=self.task.depth + 1, effort=self.effort if effort is None else effort)
        return self.runtime.run_agent(sub, self.run)

    # -- approvals ---------------------------------------------------------
    def approve(self, title: str, detail: dict | None = None) -> None:
        """Blocks until the user approves/denies in UI/TUI/Telegram. Raises ApprovalDenied."""
        from . import approvals
        if not approvals.request(self.run, self.spec.id, title, detail or {}):
            raise ApprovalDenied(title)

    # -- memory ------------------------------------------------------------
    @property
    def memory(self):
        from ..memory import store
        return store

    # -- report ------------------------------------------------------------
    def report(self, summary: str, result: Any = "", status: str = "success", **kw) -> dict:
        # (not the Main Agent's final answer: it DESCRIBES work — "handles missing inputs gracefully" made a
        #  successful tool build look blocked, 16.21)
        if status == "success" and self.spec.id != "main_agent" \
                and _BLOCKED_RE.search(f"{summary}\n{str(result)[:3000]}"):
            status = "failed"   # the agent itself says it had nothing to work on: never a ✓
            kw.setdefault("errors", []).append("blocked: the required input was missing")
        r = Report(agent_id=self.spec.id, task_id=self.task.task_id, status=status, summary=summary, result=result,
                   tool_calls=self.tool_log, delegated_to=self.delegated,
                   files_created=kw.pop("files_created", self.files_created),
                   files_modified=kw.pop("files_modified", self.files_modified),
                   duration_s=round(time.time() - self.started, 2), **kw)
        return r.model_dump()


_BLOCKED_RE = re.compile(
    r"(?i)\b(blocked upstream|no (problem statement|input|data|code) (was |is )?(provided|available|given)|"
    r"(cannot|can't|could not|couldn't) (proceed|continue) without|(is|was|are) missing (the )?(required )?input\b|"
    r"nothing to (work on|solve|paste|analy[sz]e))")


def get_ctx(config: dict) -> AgentContext:
    return config["configurable"]["ctx"]


def _is_custom(name: str) -> bool:
    td = TOOLS.get(name)
    return bool(td) and td.description.startswith("[custom]")


def _short_args(args: dict, n: int = 300) -> dict:
    """Tool args stay a dict (UI, tests and graphs read them); only long string values are shortened."""
    return {k: (v[:n] + f"… (+{len(v) - n} chars)" if isinstance(v, str) and len(v) > n else v)
            for k, v in (args or {}).items()}


def _short(v: Any, n: int = 300) -> Any:
    s = v if isinstance(v, str) else repr(v)
    if len(s) <= n:
        return v
    return s[:n] + f"… (+{len(s) - n} chars)"
