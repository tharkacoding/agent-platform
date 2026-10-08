"""Terminal operator — runs shell commands and small file edits like a CLI coding assistant.

START → intake → operate (ReAct: run_command / fs_* / search, approval per non-read-only command in host mode)
      → verify (re-run the last failing command once if the model fixed something) → finalize → END

Offline (no LLM): executes commands written in `backticks`, after "run", and handles
"create <file> that prints <text>" + "run it".
"""
from __future__ import annotations

import json
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..core.sdk import ApprovalDenied, archetype, get_ctx
from . import _common as c

TOOLS = ["run_command", "fs_list", "fs_read", "fs_write", "fs_patch", "search_code"]
KEEP_OUTPUTS = 6


class TermState(TypedDict, total=False):
    task: dict
    report: dict
    goal: str
    cwd: str
    planned: list
    log: list
    answer: str
    denied: list


def offline_commands(goal: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(commands, files_to_write) for simple goals without an LLM."""
    cmds = [m.strip() for m in re.findall(r"`([^`]+)`", goal) if m.strip()]
    files: list[tuple[str, str]] = []
    m = re.search(r"(?i)create\s+(?:a\s+)?(?:file\s+)?([\w./-]+\.py)\b.*?\bprints?\s+[\"']?([^\"'\n,]+?)[\"']?"
                  r"(?:\s*(?:,|\band\b|\bthen\b)|$)", goal)
    if m:
        name, text = m.group(1), m.group(2).strip()
        files.append((name, f"print({text!r})\n"))
        if re.search(r"(?i)\brun (it|the file|this)\b", goal) and not cmds:
            cmds.append(f"python {name}")
    if not cmds:
        for m2 in re.finditer(r"(?i)\brun\s+([a-z][\w.-]*(?:\s+[-\w./:=]+){0,6})", goal):
            cand = m2.group(1).strip()
            if not re.match(r"(?i)(it|the|this|a|an|my)\b", cand):
                cmds.append(cand)
    return list(dict.fromkeys(cmds)), files


def intake(state: TermState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("intake", "Understanding the terminal task")
    goal = c.instruction(state)
    m = re.search(r"(/(?:workspace|shared|mounts)(?:/[\w.\-]+)*)", goal)
    cwd = m.group(1) if m and not re.search(r"\.\w{1,5}$", m.group(1)) else "/workspace"
    cmds, files = offline_commands(goal)
    c.emit_plan(ctx, {"cwd": cwd, "commands": cmds, "files": [f for f, _ in files]}, "Terminal plan")
    return {"goal": goal, "cwd": cwd, "planned": [{"cmds": cmds, "files": files}], "log": [], "denied": []}


def _record(ctx, log: list, name: str, args: dict, out) -> None:
    entry = {"tool": name, "args": {k: v for k, v in args.items() if k != "content"}}
    if isinstance(out, dict):
        entry.update({k: out.get(k) for k in ("exit_code", "stdout", "stderr", "error", "ok", "path") if k in out})
    log.append(entry)


def _call(ctx, state: TermState, name: str, args: dict, log: list):
    try:
        out = ctx.call_tool(name, **args)
    except ApprovalDenied as e:
        state.setdefault("denied", []).append(str(e))
        out = {"error": f"the user denied this action: {e}"}
    except Exception as e:  # noqa: BLE001 - surface to the model, keep going
        out = {"error": f"{type(e).__name__}: {e}"}
    _record(ctx, log, name, args, out)
    return out


def operate(state: TermState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("operate", "Running commands")
    log, cwd, goal = list(state.get("log", [])), state["cwd"], state["goal"]
    if c.is_mock(ctx):
        plan = state["planned"][0]
        for name, body in plan["files"]:
            path = name if name.startswith("/") else f"{cwd.rstrip('/')}/{name}"
            _call(ctx, state, "fs_write", {"path": path, "content": body}, log)
        for cmd in plan["cmds"] or (["ls"] if re.search(r"(?i)\b(list|show) files\b", goal) else []):
            _call(ctx, state, "run_command", {"cmd": cmd, "cwd": cwd, "timeout": 120}, log)
        return {"log": log, "answer": ""}

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    names = [n for n in TOOLS if c.can(ctx, n)]
    model = ctx.role_model("coder").bind_tools(ctx.lc_tools(names))
    from .. import config as cfg
    host = getattr(cfg, "RUNNER_ON_HOST", False)
    sys = c.system(ctx, (
        "You operate a terminal for the user, like a CLI coding assistant. Use run_command for shell commands "
        f"({'Git Bash on Windows (bash syntax); ' if host else ''}cwd defaults to {cwd}), fs_write/fs_patch to create "
        "or edit files, fs_read/fs_list/search_code to inspect. Work step by step: run, read the output, fix, re-run. "
        "Paths look like /workspace/...; stay inside /workspace, /shared or /mounts. Some commands need the user's "
        "approval — if one is denied, don't retry it; explain instead. When finished, reply with a concise summary "
        "that includes the key command outputs, and make NO tool calls."))
    msgs: list = [SystemMessage(sys), HumanMessage(f"Task: {goal}\nWorking directory: {cwd}\n{c.context_text(state, 3000)}")]
    steps_max = max(3, int(c.budget(ctx).get("react_steps", 12)))
    answer = ""
    from ..core import supervisor
    sup_hist: list = []
    for step in range(steps_max):
        ctx.run.check_cancel()
        if supervisor.react_checkpoint(ctx, goal, sup_hist, step, msgs):
            answer = "Stopped by the Main Agent: no progress (see the supervisor note in the timeline)."
            break
        tool_idx = [i for i, m in enumerate(msgs) if isinstance(m, ToolMessage)]
        for i in tool_idx[:-KEEP_OUTPUTS]:
            if not str(msgs[i].content).startswith("[trimmed"):
                msgs[i] = ToolMessage(content=f"[trimmed: {str(msgs[i].content)[:160]}…]", tool_call_id=msgs[i].tool_call_id)
        ai = model.invoke(msgs)
        msgs.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            answer = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
            break
        for tc in calls:
            name, args = tc["name"], dict(tc.get("args") or {})
            if name == "run_command":
                args.setdefault("cwd", cwd)
            out = _call(ctx, state, name, args, log) if name in names else {"error": f"tool {name} unavailable"}
            supervisor.note(sup_hist, name, args, out)
            msgs.append(ToolMessage(content=c.short(out, 5000), tool_call_id=tc["id"]))
    else:
        answer = "Step budget exhausted — partial result below."
    return {"log": log, "answer": answer, "denied": state.get("denied", [])}


def verify(state: TermState, config) -> dict:
    """If the last command failed but files were changed after it, re-run it once to confirm."""
    ctx = get_ctx(config)
    log = list(state.get("log", []))
    runs = [i for i, e in enumerate(log) if e["tool"] == "run_command"]
    if not runs:
        return {}
    last = log[runs[-1]]
    if last.get("exit_code") in (0, None) or not any(e["tool"] in ("fs_write", "fs_patch") for e in log[runs[-1]:]):
        return {}
    ctx.node("verify", "Re-running the failed command after the fix")
    _call(ctx, state, "run_command", dict(last["args"]), log)
    return {"log": log}


def finalize(state: TermState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("finalize", "Summarising")
    log = state.get("log", [])
    blocks, errors, files = [], [], []
    for e in log:
        if e["tool"] == "run_command":
            out = (e.get("stdout") or "") + (("\n" + e["stderr"]) if e.get("stderr") else "")
            if e.get("error"):
                out = e["error"]
                errors.append(e["error"])
            blocks.append(f"$ {e['args'].get('cmd')}\n{out.strip()[:3000]}\n[exit {e.get('exit_code')}]")
        elif e["tool"] in ("fs_write", "fs_patch") and not e.get("error"):
            files.append(e["args"].get("path"))
    transcript = "\n\n".join(blocks)
    answer = state.get("answer") or ("Ran " + str(len(blocks)) + " command(s)." if blocks else "Nothing to run.")
    result = answer + (f"\n\n```console\n{transcript}\n```" if transcript else "")
    failed_cmds = [e for e in log if e["tool"] == "run_command" and (e.get("exit_code") not in (0, None) or e.get("error"))]
    status = "success" if log and not failed_cmds else ("partial" if log else "failed")
    if state.get("denied"):
        status = "partial"
    return {"report": ctx.report(answer.split("\n")[0][:300], result=result, status=status,
                                 errors=errors + [f"denied: {d}" for d in state.get("denied", [])],
                                 files_modified=[f for f in files if f],
                                 artifacts=[{"kind": "file", "title": "Terminal transcript", "content": transcript}]
                                 if transcript else [])}


@archetype("terminal", "Terminal operator: intake → ReAct shell/file loop (approval-gated) → verify → finalize")
def build(spec):
    g = StateGraph(TermState)
    for n, fn in [("intake", intake), ("operate", operate), ("verify", verify), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "intake")
    g.add_edge("intake", "operate")
    g.add_edge("operate", "verify")
    g.add_edge("verify", "finalize")
    g.add_edge("finalize", END)
    return g
