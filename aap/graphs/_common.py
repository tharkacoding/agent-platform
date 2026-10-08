"""Helpers shared by all archetype graphs (not an archetype itself)."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional, TypedDict

from ..core.sdk import AgentContext, ApprovalDenied, PermissionDenied

FILE_RE = re.compile(r"(?:(?:/[\w.\-]+)+|[\w.\-]+/[\w./\-]+|[\w\-]+)\.(?:py|js|jsx|ts|tsx|json|md|txt|html|css|"
                     r"cpp|cc|c|h|hpp|java|kt|go|rs|rb|sql|csv|yaml|yml|toml|sh)\b")
URL_RE = re.compile(r"https?://[^\s)\"'>]+")
CODE_BLOCK_RE = re.compile(r"```(?:[\w+\-]*)\n(.*?)```", re.S)


class BaseState(TypedDict, total=False):
    task: dict
    report: dict


def is_mock(ctx: AgentContext) -> bool:
    return ctx.llm().mode == "mock"


def budget(ctx: AgentContext) -> dict:
    """Effort-tier profile (react_steps, fix_attempts, review_loops, ...); {} if the effort module isn't available."""
    try:
        return dict(ctx.budget)
    except Exception:  # noqa: BLE001
        return {}


def instruction(state: dict) -> str:
    return (state.get("task") or {}).get("instruction", "")


def context_text(state: dict, limit: int = 6000) -> str:
    """Upstream reports flattened into text for prompts."""
    parts = []
    for r in (state.get("task") or {}).get("context", []) or []:
        if not isinstance(r, dict) or "agent_id" not in r:   # skip history / exec_state entries
            continue
        res = r.get("result", "")
        if not isinstance(res, str):
            res = json.dumps(res, default=str)[:2000]
        parts.append(f"[{r.get('agent_id', '?')}] {r.get('summary', '')}\n{res}")
    return "\n\n".join(parts)[:limit]


OPERATING_RULES = (
    "OPERATING RULES: (1) Never invent facts, files, URLs, numbers or tool results; if unsure, check with a tool or "
    "say it is unknown. (2) Verify before claiming success (run it, read it back, look at the page). (3) Do the "
    "user's actual request; if something is ambiguous, state a one-line assumption and continue. (4) If an approach "
    "failed, change strategy instead of repeating it. (5) Lead with the result, then only the essentials.")


def system(ctx: AgentContext, role_text: str) -> str:
    from .. import tracing   # a prompt edited in LangSmith's Prompt Hub wins (Settings → Tracing); code is the fallback
    base = tracing.agent_prompt(ctx.spec) or ctx.spec.system_prompt or f"You are {ctx.spec.name}. {ctx.spec.description}"
    steer = ctx.run.steer_block() if getattr(ctx.run, "steer", None) else ""   # live orders from the user
    modes = getattr(ctx.run, "directives", "")                                # /slash modes of this request
    from ..skills import methodology                                          # Superpowers skills (Settings switch)
    meth = methodology.hint(ctx.spec.graph) if "skill_read" in ctx.spec.tools else ""
    return (f"{base}\n\n{role_text}\n\n{OPERATING_RULES}".strip() + (f"\n\n{meth}" if meth else "")
            + (f"\n\n{modes}" if modes else "")
            + (f"\n\n{steer}" if steer else ""))


def level(ctx: AgentContext) -> int:
    """Effort tier 0-4 (2 when unknown)."""
    return int(budget(ctx).get("level", 2))


def optional_pass(ctx: AgentContext) -> bool:
    """Optional LLM passes (critique, self-review, reflection) only run at tier >= 2."""
    return level(ctx) >= 2


def _trim(ctx: AgentContext, text: str) -> str:
    try:
        return ctx.trim(text)
    except Exception:  # noqa: BLE001
        return text


def _llm_kw(ctx: AgentContext, role: Optional[str], min_tokens: int) -> dict:
    b = budget(ctx)
    mt = b.get("max_tokens")
    return {"role": role or b.get("model_role", "worker"),
            "max_tokens": max(int(mt), min_tokens) if mt else None}


def ask_json(ctx: AgentContext, role_text: str, user: str, fallback: Callable[[], dict],
             validate: Optional[Callable[[dict], bool]] = None, role: Optional[str] = None,
             trim: bool = True, min_tokens: int = 300) -> dict:
    """JSON LLM call scaled by the effort budget: tier model role, max_tokens and trimmed context."""
    return ctx.llm().json(system(ctx, role_text), _trim(ctx, user) if trim else user, fallback=fallback,
                          validate=validate, **_llm_kw(ctx, role, min_tokens))


def persona(ctx: AgentContext) -> str:
    """Soul block for agents with a default soul (teacher, engineer, researcher, coach...). '' otherwise.
    The conversation's chosen soul wins over the agent default. Sized by the effort tier (aap/souls)."""
    if not getattr(ctx.spec, "soul", ""):
        return ""
    try:
        from .. import souls
        sid = souls.resolve(getattr(ctx.run, "conversation_id", ""), ctx.spec.soul)
        return souls.system_prefix(sid, level(ctx), tools=bool(ctx.spec.tools)) + "\n\n"
    except Exception:  # noqa: BLE001
        return ""


def ask_text(ctx: AgentContext, role_text: str, user: str, fallback: Callable[[], str], role: Optional[str] = None,
             trim: bool = True, min_tokens: int = 200, soul: bool = True) -> str:
    """Free-text LLM call. User-facing text gets the agent's soul (pass soul=False for code/prompt generation)."""
    sys_text = system(ctx, role_text)
    if soul and not is_mock(ctx):
        sys_text = persona(ctx) + sys_text
    return ctx.llm().complete(sys_text, _trim(ctx, user) if trim else user, fallback=fallback,
                              **_llm_kw(ctx, role, min_tokens))


def tool(ctx: AgentContext, name: str, **args) -> Any:
    """call_tool that never raises for permission problems (returns {'error'}), but propagates ApprovalDenied."""
    if name not in ctx.spec.tools:
        return {"error": f"tool {name} not available to {ctx.spec.id}"}
    try:
        return ctx.call_tool(name, **args)
    except PermissionDenied as e:
        return {"error": f"permission denied: {e}"}


def ok(out: Any) -> bool:
    return isinstance(out, dict) and not out.get("error")


def can(ctx: AgentContext, name: str) -> bool:
    return name in ctx.spec.tools


def delegate(ctx: AgentContext, agent_id: str, text: str, context: list | None = None,
             effort: int | None = None) -> dict:
    """Delegate and always return a report dict (failed report on error)."""
    try:
        rep = ctx.delegate(agent_id, text, context=context, effort=effort)
        return rep.model_dump() if hasattr(rep, "model_dump") else dict(rep)
    except ApprovalDenied:
        raise
    except Exception as e:  # noqa: BLE001
        return {"agent_id": agent_id, "status": "failed", "summary": f"delegation failed: {e}",
                "result": "", "errors": [str(e)]}


def emit_plan(ctx: AgentContext, plan: Any, message: str = "plan") -> None:
    ctx.run.emit("plan", ctx.spec.id, "info", message, plan=plan, task_id=ctx.task.task_id)


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:n].strip("_")) or "task"


def files_in(text: str) -> list[str]:
    seen, out = set(), []
    for m in FILE_RE.finditer(text):
        f = m.group(0)
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def code_blocks(text: str) -> list[str]:
    return [b.strip("\n") for b in CODE_BLOCK_RE.findall(text or "")]


def bullets(text: str, n: int = 8) -> list[str]:
    """Pull bullet/numbered lines from text (used to turn free text into lists)."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)", line)
        if m:
            out.append(m.group(1))
    return out[:n]


def sentences(text: str, n: int = 3) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p for p in parts if p][:n]


def short(v: Any, n: int = 4000) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str, indent=1)
    return s if len(s) <= n else s[:n] + f"\n… (+{len(s) - n} chars)"


def keywords(text: str, n: int = 6) -> list[str]:
    stop = set("the a an and or of to for in on with my me i we you your is are be that this it as at by from "
               "please can could would should do does will using use make create build need want about into "
               "some any all what how why which who when where".split())
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+#\-]{2,}", text.lower()) if w not in stop]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:n]
