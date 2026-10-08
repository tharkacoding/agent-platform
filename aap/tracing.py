"""LangSmith tracing (TODO N14): see every agent's graph steps AND every model call (prompt, answer, tokens, model,
latency) in smith.langchain.com, grouped per run — so mistakes are easy to find and fix.

OFF by default. Tracing sends prompts and answers to LangSmith's cloud: it needs the owner's API key and the switch
(Settings → Tracing). Settings: langsmith_enabled, langsmith_api_key (secret), langsmith_project (default "agentos").
- LangGraph runs/nodes + LangChain models are traced by the env vars set in apply();
- the OpenAI-SDK client AgentOS uses for most calls is wrapped (wrap_client) so those calls show up too;
- every graph run gets a name (agent id), tags (agent, tier) and metadata (run id, task id, request preview).
"""
from __future__ import annotations

import logging
import os

from . import settings

log = logging.getLogger("aap.tracing")
_ENV = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT")


def on() -> bool:
    return bool(settings.get("langsmith_enabled")) and bool(settings.get("langsmith_api_key"))


def apply() -> dict:
    """Push the settings into the environment LangChain/LangGraph/LangSmith read (call at boot + on every change)."""
    was = os.environ.get("LANGSMITH_TRACING") == "true"
    if on():
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.get("langsmith_api_key")
        os.environ["LANGSMITH_PROJECT"] = settings.get("langsmith_project") or "agentos"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"          # older LangChain versions read the LANGCHAIN_* names
        os.environ["LANGCHAIN_API_KEY"] = settings.get("langsmith_api_key")
        os.environ["LANGCHAIN_PROJECT"] = settings.get("langsmith_project") or "agentos"
    else:
        for k in _ENV:
            os.environ.pop(k, None)
        os.environ["LANGSMITH_TRACING"] = "false"
    if was != on():   # the cached OpenAI client was built with/without the wrapper: rebuild it
        try:
            from .llm import llm
            llm.reset()
        except Exception:  # noqa: BLE001
            pass
    return status()


def status() -> dict:
    return {"enabled": bool(settings.get("langsmith_enabled")), "has_key": bool(settings.get("langsmith_api_key")),
            "active": on(), "project": settings.get("langsmith_project") or "agentos",
            "url": "https://smith.langchain.com"}


def wrap_client(client):
    """The OpenAI-SDK client with LangSmith tracing around every call when tracing is active."""
    if not on():
        return client
    try:
        from langsmith.wrappers import wrap_openai
        return wrap_openai(client)
    except Exception as e:  # noqa: BLE001 - tracing must never break a model call
        log.warning("LangSmith wrapper unavailable: %s", e)
        return client


def run_config(spec, task, run) -> dict:
    """name / tags / metadata for one graph run (merged into the LangGraph config) — empty when tracing is off."""
    if not on():
        return {}
    return {"run_name": f"{spec.id}", "tags": ["agentos", f"agent:{spec.id}", f"graph:{spec.graph}",
                                              f"tier:{getattr(task, 'effort', -1)}"],
            "metadata": {"run_id": run.run_id, "task_id": task.task_id, "agent": spec.id, "graph": spec.graph,
                         "parent_agent": getattr(task, "parent_agent", ""), "depth": getattr(task, "depth", 0),
                         "request": (task.instruction or "")[:300]}}


def test() -> dict:
    """Ask LangSmith whether the key works (lists one project)."""
    key = settings.get("langsmith_api_key")
    if not key:
        return {"ok": False, "error": "no API key set"}
    try:
        from langsmith import Client
        projects = list(Client(api_key=key).list_projects(limit=1))
        return {"ok": True, "message": "LangSmith key works" + (f" (project: {projects[0].name})" if projects else "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


# ---------------------------------------------------------------------------------------------- Prompt Hub
# Agent system prompts can live in LangSmith's Prompt Hub as `agentos-<agent-id>`: push the current ones once
# (push_prompts), edit them in LangSmith (Playground / Prompts), and agents use the edited text within TTL seconds.
# The prompt written in code stays the fallback (hub off, key missing, prompt absent, any error).
_TTL = 120.0
_prompt_cache: dict[str, tuple[float, str]] = {}


def hub_name(agent_id: str) -> str:
    return "agentos-" + agent_id.replace("_", "-")


def _client():
    from langsmith import Client
    return Client(api_key=settings.get("langsmith_api_key"))


def _text_of(prompt) -> str:
    """The system text of a pulled prompt object (a message prompt or a template)."""
    msgs = getattr(prompt, "messages", None) or []
    for m in msgs:
        c = getattr(m, "content", None)
        if isinstance(c, str) and c.strip():
            return c
        tpl = getattr(getattr(m, "prompt", None), "template", None)
        if isinstance(tpl, str) and tpl.strip():
            return tpl
    tpl = getattr(prompt, "template", None)
    return tpl if isinstance(tpl, str) else ""


def agent_prompt(spec) -> str:
    """The prompt LangSmith has for this agent ('' = use the one in code)."""
    import time
    if not (on() and settings.get("langsmith_prompts")):
        return ""
    hit = _prompt_cache.get(spec.id)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    text = ""
    try:
        text = _text_of(_client().pull_prompt(hub_name(spec.id)))
    except Exception:  # noqa: BLE001 - absent prompt / network: the code's prompt is used
        text = ""
    _prompt_cache[spec.id] = (time.time(), text)
    return text


def push_prompts() -> dict:
    """Push every agent's current system prompt to the Prompt Hub (private) so it can be edited in LangSmith."""
    if not on():
        return {"ok": False, "error": "turn tracing on and add the API key first"}
    from langchain_core.messages import SystemMessage
    from langchain_core.prompts import ChatPromptTemplate
    from .agents.prebuilt.catalog import PREBUILT
    cli, pushed, failed = _client(), [], []
    for a in PREBUILT:
        text = a.system_prompt or f"You are the {a.name}. {a.description}"
        try:   # message objects, not a template: braces in prompts are not variables
            cli.push_prompt(hub_name(a.id), object=ChatPromptTemplate.from_messages([SystemMessage(content=text)]),
                            description=f"AgentOS agent '{a.id}' — system prompt", is_public=False)
            pushed.append(a.id)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{a.id}: {type(e).__name__}: {str(e)[:100]}")
    return {"ok": not failed, "pushed": len(pushed), "failed": failed[:5], "prefix": "agentos-"}


def recent_runs(limit: int = 10, errors_only: bool = False) -> dict:
    """Recent top-level traces (for the Self-Improvement Agent): name, status, error, latency, tokens, link."""
    if not on():
        return {"error": "LangSmith tracing is off (Settings → Tracing)"}
    try:
        runs = _client().list_runs(project_name=settings.get("langsmith_project") or "agentos", is_root=True,
                                   error=True if errors_only else None, limit=max(1, min(int(limit), 50)))
        out = []
        for r in runs:
            secs = (r.end_time - r.start_time).total_seconds() if r.end_time and r.start_time else None
            out.append({"agent": r.name, "status": r.status, "error": (r.error or "")[:200],
                        "seconds": round(secs, 1) if secs is not None else None, "tokens": r.total_tokens,
                        "run_id": (r.metadata or {}).get("run_id") if hasattr(r, "metadata") else None,
                        "trace_id": str(r.trace_id)})
        return {"runs": out}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
