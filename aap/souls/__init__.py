"""Souls (switchable personalities) + workspace persona files — OpenClaw/Hermes-style, written from scratch.

* `data/souls/<id>/SOUL.md` — a selectable personality (frontmatter: name, icon, description, tone, best_for, builtin).
  Built-ins are created on first use and never overwritten; users can edit them or add their own.
* `data/workspace_md/` — AGENTS.md (operating rules), USER.md (about the user, 4k cap), IDENTITY.md (assistant
  name/emoji/vibe — the source of truth for the user-changeable assistant name), TOOLS.md, HEARTBEAT.md, MEMORY.md.
* `system_prefix(soul_id, effort)` assembles the persona block for user-facing LLM calls, sized by the seriousness
  tier (tier 0-1: 2-3 lines only, to save tokens). Planning/JSON calls stay soul-free.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import config

SOULS_DIR = config.SOULS_DIR
WS_DIR = config.WORKSPACE_MD_DIR
WORKSPACE_FILES = ["AGENTS", "USER", "IDENTITY", "TOOLS", "HEARTBEAT", "MEMORY"]
USER_MAX = 4000          # OpenClaw keeps USER.md on its own 4k budget
FILE_MAX = 20000         # per-file cap (OpenClaw bootstrapMaxChars default)
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# ------------------------------------------------------------------------------------------------ templates
BUILTIN_SOULS: dict[str, dict] = {
    "default": dict(name="Balanced", icon="✨", tone="warm, clear, practical",
                    description="Balanced personal assistant: helpful, honest, gets things done.",
                    best_for=["everyday tasks", "mixed requests"], body="""\
# Soul: Balanced assistant
- Be genuinely useful: lead with the answer or the outcome, then the details that matter.
- Be honest about uncertainty and failures; never pretend work was done.
- Match the user's language and energy; keep it short unless depth is asked for.
- Prefer doing over describing: when agents can do the work, delegate it.
- Respect the user's time, privacy and their computer: ask before anything risky or irreversible.
"""),
    "teacher": dict(name="Teacher", icon="🧑‍🏫", tone="patient, encouraging, Socratic",
                    description="Socratic teacher: builds understanding step by step and checks it.",
                    best_for=["learning", "explanations", "exam prep", "curriculum"], body="""\
# Soul: Teacher
- First find out what the learner already knows; build from there.
- Explain with one clear idea at a time, a concrete example, then a slightly harder one.
- Ask guiding questions instead of dumping answers when the learner can get there themselves.
- Check understanding with a short question or mini-quiz at the end of each idea.
- Normalise mistakes; point out exactly what was right before correcting what was wrong.
- Summarise the key takeaway in one line and suggest the next thing to practise.
"""),
    "engineer": dict(name="Engineer", icon="🛠️", tone="precise, terse, evidence-driven",
                     description="Senior engineer: precise, terse, test-first, shows diffs.",
                     best_for=["coding", "debugging", "code review", "architecture"], body="""\
# Soul: Engineer
- Be precise and terse. Code, commands and file paths over prose.
- Reproduce before fixing; write or run a test that proves the change works.
- Show what changed (files, diff summary) and how it was verified.
- Call out risks, edge cases and anything you could not verify.
- No speculative refactors; change only what the task needs.
"""),
    "researcher": dict(name="Researcher", icon="🔬", tone="careful, sourced, calibrated",
                       description="Careful researcher: cites sources and states confidence.",
                       best_for=["research", "comparisons", "fact-finding", "reports"], body="""\
# Soul: Researcher
- Separate facts, sources and your own inference; cite where each fact came from.
- State confidence (high/medium/low) and what would change your mind.
- Prefer primary sources; flag outdated or conflicting information.
- Structure findings: answer first, evidence next, open questions last.
"""),
    "coach": dict(name="Coach", icon="🏃", tone="motivating, structured, accountable",
                  description="Coach: turns goals into plans, keeps you accountable.",
                  best_for=["planning", "habits", "productivity", "learning plans"], body="""\
# Soul: Coach
- Turn vague goals into concrete next actions with dates and sizes.
- Celebrate progress specifically; be direct about what slipped and why.
- Keep plans realistic: protect rest, plan for obstacles, small daily wins.
- End with the single next step and when you will check in.
"""),
    "concise": dict(name="Concise", icon="⚡", tone="minimal",
                    description="Minimal words. Answers only, no filler.",
                    best_for=["quick answers", "token saving"], body="""\
# Soul: Concise
- Answer in as few words as correctness allows. Bullets over paragraphs.
- No greetings, no restating the question, no closing offers.
"""),
    "friend": dict(name="Friend", icon="😊", tone="warm, casual, supportive",
                   description="Warm, casual friend who still gets things done.",
                   best_for=["casual chat", "motivation", "brainstorming"], body="""\
# Soul: Friend
- Talk like a supportive friend: casual, warm, a little playful when it fits.
- Listen first; reflect back what you heard before giving advice.
- Still be honest and useful — kindness is not flattery.
"""),
}

DEFAULT_FILES: dict[str, str] = {
    "IDENTITY": """---
name: AgentOS
emoji: ✨
vibe: calm, capable, quietly proactive
---
# Identity
This is your assistant's identity. Change the name/emoji/vibe above (or in Settings → Assistant).
""",
    "USER": """# About the user
<!-- Stable facts about you: name, timezone, preferences, communication style, active projects.
     Kept under 4,000 characters. Long-term memory facts are appended automatically when prompting. -->
- Name:
- Timezone:
- Preferences:
""",
    "AGENTS": """# Operating rules
1. Understand the request, pick the right agents, and delegate; don't do everything yourself.
2. Match effort to seriousness: quick things fast and cheap, critical things carefully and with approval.
3. Never take irreversible or external actions (sending, buying, deleting, pushing) without approval.
4. Work inside the sandbox; only touch folders the user shared.
5. Report honestly: what was done, what failed, what is left.
""",
    "TOOLS": """# Tool notes
<!-- Conventions for tools: preferred commands, project paths, test commands, sites to use or avoid. -->
- Shared folder: /shared  · Workspace: /workspace
""",
    "HEARTBEAT": """# Heartbeat checklist
<!-- Run periodically by the optional 'heartbeat' schedule (disabled by default). Keep it short. -->
- Anything due today from my schedules or goals?
- Any failed runs or pending approvals I should know about?
- One useful suggestion for today (optional).
""",
    "MEMORY": """# Memory
Long-term memory lives in the Memory page (SQLite + FTS) and is mirrored to `data/memory/*.md`.
Add durable notes there; this file is informational.
""",
}


# ------------------------------------------------------------------------------------------------ helpers
def _parse(md: str) -> tuple[dict, str]:
    m = _FM.match(md or "")
    if not m:
        return {}, md or ""
    meta: dict = {}
    try:
        import yaml  # type: ignore
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                v = v.strip().strip('"\'')
                meta[k.strip()] = [x.strip() for x in v.strip("[]").split(",") if x.strip()] if v.startswith("[") else v
    return (meta if isinstance(meta, dict) else {}), m.group(2)


def _yaml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(str(x).replace(",", " ") for x in v) + "]"
    s = str(v).replace("\n", " ")
    return f'"{s}"' if (":" in s or s.startswith(("[", "{", "#", "-"))) else s


def _render(meta: dict, body: str) -> str:
    fm = "\n".join(f"{k}: {_yaml_val(v)}" for k, v in meta.items())
    return f"---\n{fm}\n---\n{body.lstrip()}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "soul"


def ensure_defaults() -> None:
    SOULS_DIR.mkdir(parents=True, exist_ok=True)
    WS_DIR.mkdir(parents=True, exist_ok=True)
    for sid, d in BUILTIN_SOULS.items():
        f = SOULS_DIR / sid / "SOUL.md"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            meta = {k: d[k] for k in ("name", "icon", "description", "tone", "best_for")} | {"builtin": True}
            f.write_text(_render(meta, d["body"]), encoding="utf-8")
    for name, text in DEFAULT_FILES.items():
        f = WS_DIR / f"{name}.md"
        if not f.exists():
            f.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------------------------------------ souls CRUD
def _soul_from_dir(d: Path) -> dict | None:
    f = d / "SOUL.md"
    if not f.exists():
        return None
    meta, body = _parse(f.read_text(encoding="utf-8", errors="replace"))
    bf = meta.get("best_for") or []
    return {"id": d.name, "name": str(meta.get("name") or d.name.title()), "icon": str(meta.get("icon") or "✨"),
            "description": str(meta.get("description") or ""), "tone": str(meta.get("tone") or ""),
            "best_for": bf if isinstance(bf, list) else [str(bf)], "builtin": bool(meta.get("builtin")),
            "source": str(meta.get("source") or ""), "body": body}


def list_souls() -> list[dict]:
    ensure_defaults()
    out = [s for d in sorted(SOULS_DIR.iterdir()) if d.is_dir() and (s := _soul_from_dir(d))]
    order = list(BUILTIN_SOULS)
    return sorted(out, key=lambda s: (order.index(s["id"]) if s["id"] in order else 99, s["name"].lower()))


def get_soul(soul_id: str) -> dict | None:
    ensure_defaults()
    if not soul_id or not _ID.match(soul_id):
        return None
    return _soul_from_dir(SOULS_DIR / soul_id)


def save_soul(soul_id: str, data: dict) -> dict:
    ensure_defaults()
    soul_id = soul_id or slug(data.get("name", ""))
    if not _ID.match(soul_id):
        raise ValueError("soul id must be lowercase letters, digits, - or _")
    old = get_soul(soul_id) or {}
    meta = {"name": data.get("name") or old.get("name") or soul_id.title(),
            "icon": data.get("icon") or old.get("icon") or "✨",
            "description": data.get("description", old.get("description", "")),
            "tone": data.get("tone", old.get("tone", "")),
            "best_for": data.get("best_for", old.get("best_for", [])) or []}
    if old.get("builtin"):
        meta["builtin"] = True
    if data.get("source") or old.get("source"):
        meta["source"] = data.get("source") or old.get("source")
    body = data.get("body", old.get("body", f"# Soul: {meta['name']}\n"))[:FILE_MAX]
    d = SOULS_DIR / soul_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SOUL.md").write_text(_render(meta, body), encoding="utf-8")
    return get_soul(soul_id)


def delete_soul(soul_id: str) -> None:
    s = get_soul(soul_id)
    if not s:
        raise KeyError(soul_id)
    if s["builtin"]:
        raise ValueError("built-in souls can be edited but not deleted")
    shutil.rmtree(SOULS_DIR / soul_id)


# ------------------------------------------------------------------------------------------------ workspace files
def get_file(name: str) -> str:
    ensure_defaults()
    name = name.upper().removesuffix(".MD")
    if name not in WORKSPACE_FILES:
        raise KeyError(name)
    f = WS_DIR / f"{name}.md"
    return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


def put_file(name: str, content: str) -> str:
    ensure_defaults()
    name = name.upper().removesuffix(".MD")
    if name not in WORKSPACE_FILES:
        raise KeyError(name)
    cap = USER_MAX if name == "USER" else FILE_MAX
    (WS_DIR / f"{name}.md").write_text(content[:cap], encoding="utf-8")
    return get_file(name)


def list_files() -> list[dict]:
    return [{"name": n, "filename": f"{n}.md", "content": get_file(n), "max_chars": USER_MAX if n == "USER"
             else FILE_MAX} for n in WORKSPACE_FILES]


# ------------------------------------------------------------------------------------------------ identity
def get_identity() -> dict:
    meta, body = _parse(get_file("IDENTITY"))

    def line(key: str) -> str:   # OpenClaw-style "- **Name:** Clawd" lines when there is no frontmatter
        m = re.search(rf"(?im)^\s*[-*]?\s*\**{key}\**\s*:\s*\**\s*(\S.*?)\s*$", body)
        return m.group(1).strip("* ") if m else ""

    name = str(meta.get("name") or line("name") or "AgentOS").strip() or "AgentOS"
    return {"name": name, "emoji": str(meta.get("emoji") or line("emoji") or "✨"),
            "vibe": str(meta.get("vibe") or line("vibe") or ""), "notes": body.strip()}


def set_identity(name: str | None = None, emoji: str | None = None, vibe: str | None = None) -> dict:
    cur = get_identity()
    meta = {"name": (name if name is not None else cur["name"]).strip()[:60] or "AgentOS",
            "emoji": (emoji if emoji is not None else cur["emoji"]).strip()[:8] or "✨",
            "vibe": (vibe if vibe is not None else cur["vibe"]).strip()[:200]}
    put_file("IDENTITY", _render(meta, "# Identity\n" + (cur["notes"].removeprefix("# Identity").strip() or
                                                          "This is your assistant's identity.") + "\n"))
    return get_identity()


def assistant_name() -> str:
    try:
        return get_identity()["name"]
    except Exception:  # noqa: BLE001
        return "AgentOS"


def user_name() -> str:
    m = re.search(r"(?im)^\s*[-*]?\s*name:\s*(\S.*)$", get_file("USER"))
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------------------------------------ active soul
def resolve(conversation_id: str = "", agent_soul: str = "") -> str:
    """Soul for a reply: conversation choice > agent default > settings default > 'default'."""
    from .. import registry, settings
    sid = ""
    if conversation_id:
        try:
            sid = registry.get_conversation_soul(conversation_id) or ""
        except Exception:  # noqa: BLE001
            sid = ""
    for cand in (sid, agent_soul, settings.get("default_soul"), "default"):
        if cand and get_soul(cand):
            return cand
    return "default"


# ------------------------------------------------------------------------------------------------ prompt assembly
def _strip_comments(t: str) -> str:
    return re.sub(r"<!--.*?-->", "", t or "", flags=re.S).strip()


def _cap(t: str, n: int) -> str:
    t = _strip_comments(t)
    return t if len(t) <= n else t[:n].rsplit("\n", 1)[0] + "\n…"


def _memory_facts(n: int) -> str:
    try:
        from ..memory import store
        facts = [m["text"] for m in store.list_all("fact")[:n]]
    except Exception:  # noqa: BLE001
        facts = []
    return "\n".join(f"- {f}" for f in facts)


def system_prefix(soul_id: str = "default", effort: int = 2, tools: bool = False) -> str:
    """Persona block for user-facing generation, sized by the seriousness tier (token saving at low tiers)."""
    ensure_defaults()
    soul = get_soul(soul_id) or get_soul("default") or {}
    ident = get_identity()
    uname = user_name()
    head = f"You are {ident['name']} {ident['emoji']}".strip()
    if ident["vibe"]:
        head += f" — {ident['vibe']}"
    head += "."
    if effort <= 1:   # 2-3 lines only
        lines = [head, f"Personality: {soul.get('name', '')} — {soul.get('description', '')}"
                       + (f" Tone: {soul['tone']}." if soul.get("tone") else "")]
        if uname:
            lines.append(f"The user's name is {uname}.")
        return "\n".join(lines)
    caps = {2: (1500, 800, 800, 0, 4), 3: (4000, USER_MAX, 3000, 2000, 10)}.get(min(effort, 3))
    soul_cap, user_cap, agents_cap, tools_cap, nfacts = caps
    parts = [head, "## Soul\n" + _cap(soul.get("body", ""), soul_cap)]
    user = _cap(get_file("USER"), user_cap)
    facts = _memory_facts(nfacts)
    if facts:
        user = (user + "\n\nKnown facts (from memory):\n" + facts)[: user_cap + 600]
    if user.strip():
        parts.append("## About the user\n" + user)
    parts.append("## Operating rules\n" + _cap(get_file("AGENTS"), agents_cap))
    if tools and tools_cap:
        parts.append("## Tool notes\n" + _cap(get_file("TOOLS"), tools_cap))
    return "\n\n".join(p for p in parts if p.strip())


# ------------------------------------------------------------------------------------------------ importers
def default_import_paths() -> list[Path]:
    home = Path.home()
    return [home / ".openclaw" / "workspace", home / ".openclaw", home / ".hermes"]


def import_from(path: str | None = None) -> dict:
    """Copy SOUL/USER/AGENTS/IDENTITY/TOOLS/HEARTBEAT.md from an OpenClaw workspace or Hermes home (sources untouched).
    The imported SOUL.md becomes a selectable soul; workspace files we already have are backed up as *.bak."""
    candidates = [Path(path).expanduser()] if path else default_import_paths()
    root = next((p for p in candidates if p.is_dir() and any((p / f"{n}.md").exists() for n in
                                                          ["SOUL"] + WORKSPACE_FILES)), None)
    if root is None:
        raise FileNotFoundError("no SOUL.md/AGENTS.md/USER.md found in " + ", ".join(map(str, candidates)))
    ensure_defaults()
    kind = "hermes" if ".hermes" in root.parts or root.name == ".hermes" else (
        "openclaw" if any(".openclaw" in p for p in root.parts) else slug(root.name))
    out: dict = {"source": str(root), "kind": kind, "soul": None, "files": []}
    soul_md = root / "SOUL.md"
    if soul_md.exists():
        meta, body = _parse(soul_md.read_text(encoding="utf-8", errors="replace"))
        sid = slug(f"{kind}-soul") if kind in ("hermes", "openclaw") else slug(kind)
        s = save_soul(sid, {"name": meta.get("name") or f"{kind.title()} soul", "icon": meta.get("icon") or "🧬",
                            "description": meta.get("description") or f"Imported from {root}",
                            "tone": meta.get("tone", ""), "best_for": meta.get("best_for") or [],
                            "source": str(soul_md), "body": body or soul_md.read_text(encoding="utf-8")})
        out["soul"] = s["id"]
    for n in ["USER", "AGENTS", "IDENTITY", "TOOLS", "HEARTBEAT"]:
        src = root / f"{n}.md"
        if not src.exists():
            continue
        dst = WS_DIR / f"{n}.md"
        if dst.exists():
            shutil.copy2(dst, WS_DIR / f"{n}.md.bak")
        text = src.read_text(encoding="utf-8", errors="replace")
        dst.write_text(text[: USER_MAX if n == "USER" else FILE_MAX], encoding="utf-8")
        out["files"].append(f"{n}.md")
    return out
