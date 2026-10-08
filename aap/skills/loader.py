"""Agent Skills (SKILL.md) support — the folder format used by OpenClaw, Hermes Agent, Claude and others.

A skill is a folder containing SKILL.md with YAML frontmatter (`name`, `description`, optional metadata) followed by
markdown instructions, plus optional resource files (scripts/, references/, assets/). We copy imported skills into
data/skills/<id>/ (read-only sources stay untouched) and expose name+description to models until a skill is used
(progressive disclosure).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _parse(md: str) -> tuple[dict, str]:
    m = _FM.match(md)
    if not m:
        return {}, md
    meta: dict = {}
    try:
        import yaml  # type: ignore
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001 - minimal "key: value" fallback
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"\'')
    return meta if isinstance(meta, dict) else {}, m.group(2)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "skill"


def _load_dir(d: Path, source: str = "") -> dict | None:
    f = d / "SKILL.md"
    if not f.exists():
        return None
    meta, body = _parse(f.read_text(encoding="utf-8", errors="replace"))
    name = str(meta.get("name") or d.name)
    res = [str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md"]
    return {"id": _slug(name), "name": name, "description": str(meta.get("description", ""))[:1000],
            "source": source or str(meta.get("source", "")), "path": str(d), "body": body, "resources": res[:200],
            "meta": {k: v for k, v in meta.items() if k not in ("name", "description")}}


def list_skills() -> list[dict]:
    out = []
    for d in sorted(config.SKILLS_DIR.iterdir()) if config.SKILLS_DIR.exists() else []:
        s = _load_dir(d, (d / ".source").read_text().strip() if (d / ".source").exists() else "")
        if s:
            out.append({k: s[k] for k in ("id", "name", "description", "source", "path")})
    return out


def get(skill_id: str) -> dict | None:
    d = config.SKILLS_DIR / skill_id
    return _load_dir(d) if d.exists() else None


def import_path(src: str, source_label: str = "") -> list[str]:
    """Import every folder containing SKILL.md under `src` (a skills root or a single skill)."""
    root = Path(src)
    if not root.exists():
        raise FileNotFoundError(src)
    found = [p.parent for p in root.rglob("SKILL.md")]
    ids = []
    for d in found:
        s = _load_dir(d)
        if not s:
            continue
        dest = config.SKILLS_DIR / s["id"]
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(d, dest, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        (dest / ".source").write_text(source_label or str(d), encoding="utf-8")
        ids.append(s["id"])
    return ids


def import_git(url: str) -> list[str]:
    if not re.match(r"^https://[\w.-]+/[\w./-]+$", url):
        raise ValueError("only https git URLs are allowed")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--depth", "1", url, tmp + "/repo"], check=True, timeout=180,
                       capture_output=True)
        return import_path(tmp + "/repo", url)


def as_agent_spec(skill: dict):
    """Wrap an imported skill as a standalone 'imported' agent (generic archetype)."""
    from ..models import AgentSpec, Permissions
    return AgentSpec(id=f"skill_{skill['id'].replace('-', '_')}", name=skill["name"].replace("-", " ").title(),
                     type="imported", category="Skills", icon="🧩", description=skill["description"][:300],
                     capabilities=[skill["id"]], tools=["skill_read", "memory_recall", "web_fetch"],
                     skills=[skill["id"]], permissions=Permissions(), graph="generic",
                     system_prompt=f"You follow the '{skill['name']}' skill. Read it with skill_read first.",
                     created_by=f"import:{skill.get('source', '')}")
