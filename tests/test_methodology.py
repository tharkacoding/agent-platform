"""Superpowers methodology: index for coding-type agents, off by switch, silent when skills aren't installed."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_meth_"))

from aap import config, settings  # noqa: E402
from aap.skills import methodology  # noqa: E402


def _skill(sid, desc):
    d = config.SKILLS_DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {sid}\ndescription: {desc}\n---\nbody", encoding="utf-8")


def test_hint_lists_installed_skills_for_the_right_graphs():
    assert methodology.hint("coding") == ""                       # nothing installed → silent
    _skill("test-driven-development", "Use when implementing any feature or bugfix, before writing code")
    _skill("systematic-debugging", "Use when encountering any bug, test failure, or unexpected behavior")
    h = methodology.hint("coding")
    assert "skill_read" in h and "test-driven-development" in h and "systematic-debugging" in h
    assert methodology.hint("research") == ""                     # not a coding-type graph


def test_switch_turns_it_off(monkeypatch):
    _skill("test-driven-development", "Use when implementing any feature")
    monkeypatch.setattr(settings, "get", lambda k, d=None: False if k == "superpowers_enabled" else d)
    assert methodology.hint("coding") == ""


def test_coding_agents_get_skill_read():
    from aap.agents.prebuilt.catalog import PREBUILT
    coder = next(a for a in PREBUILT if a.id == "coding_agent")
    assert "skill_read" in coder.tools
