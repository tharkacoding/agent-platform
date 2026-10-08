"""Self-extension tools: agents create new TOOLS and SKILLS (agents are created by the Agent Builder)."""
from __future__ import annotations

import re

from ..core import toolfactory
from ..core.sdk import TOOLS, tool


def _tool_risk(args: dict):
    return (f"create a new tool '{args.get('name')}' ({args.get('needs') or 'pure logic'}) — review its code:\n"
            f"{str(args.get('code', ''))[:1500]}")


@tool("create_tool", "Create a NEW reusable tool for all agents. `code` must define run(input: dict) -> dict "
      "(allowed imports: math, statistics, json, re, collections, itertools, functools, datetime, heapq, bisect, "
      "string, random, typing, dataclasses; urllib only when needs='network'). `params` is a JSON-schema "
      "properties object; `test_input` is an example input used to self-test before registering. Needs approval.",
      {"name": {"type": "string"}, "description": {"type": "string"}, "params": {"type": "object"},
       "code": {"type": "string"}, "needs": {"type": "string", "default": ""},
       "test_input": {"type": "object", "default": {}}}, risky=_tool_risk)
def create_tool(ctx, name: str, description: str, params: dict, code: str, needs: str = "", test_input: dict = None):
    errs = toolfactory.validate(name, code, params, needs)
    if errs:
        return {"error": "validation failed: " + "; ".join(errs[:6]), "created": False}
    from ..clients import ServiceUnavailable, runner
    try:   # self-test in the sandbox BEFORE registering (verified, not just written)
        r = runner("POST", "/python", json={"code": code, "input": test_input or {}, "timeout": 30}, timeout=50)
    except ServiceUnavailable as e:
        return {"error": f"cannot self-test (runner unavailable): {e}", "created": False}
    if not isinstance(r, dict) or not r.get("ok", False) or r.get("error"):
        return {"error": f"self-test failed: {str(r)[:400]}", "created": False}
    m = toolfactory.save(name, description, params, code, needs, created_by=ctx.spec.id, test_input=test_input)
    ctx.emit(f"New tool created: {name}", tool_created=name)
    return {"created": True, "name": name, "test_output": str(r.get("output"))[:300], "manifest": m}


@tool("create_skill", "Create a reusable SKILL (SKILL.md: instructions/know-how an agent can follow later). Use after "
      "a task worked so the method can be reused.",
      {"name": {"type": "string"}, "description": {"type": "string"}, "instructions": {"type": "string"}})
def create_skill(ctx, name: str, description: str, instructions: str):
    from .. import config
    from ..skills import loader
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]
    if not slug:
        return {"error": "invalid name"}
    d = config.SKILLS_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: {description.strip()[:300]}\n"
                                f"created_by: {ctx.spec.id}\n---\n\n{instructions.strip()}\n", encoding="utf-8")
    (d / ".source").write_text(f"agent:{ctx.spec.id}", encoding="utf-8")
    ctx.emit(f"New skill created: {slug}", skill_created=slug)
    return {"created": True, "id": slug, "path": str(d), "skill": loader.get(slug) is not None}


@tool("list_tools", "List every tool agents can use (built-in and custom) with what it does.", {})
def list_tools(ctx):
    return {"tools": [{"name": n, "needs": t.needs, "description": t.description[:160]}
                      for n, t in sorted(TOOLS.items())]}
