"""CLI unit tests: mounts override rendering and event descriptions (no Docker, no gateway needed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import mounts
from cli.api import describe_event
from cli.main import app


@pytest.fixture
def mounts_file(tmp_path, monkeypatch):
    f = tmp_path / "mounts.json"
    monkeypatch.setattr(mounts, "MOUNTS_FILE", f)
    monkeypatch.setattr(mounts, "ensure_appdata", lambda: tmp_path)
    return f


def test_mount_add_render_remove(tmp_path: Path, mounts_file):
    proj = tmp_path / "My Project"
    proj.mkdir()
    m = mounts.add(str(proj), rw=False)
    assert m["name"] == "my-project" and m["mode"] == "ro" and m["container_path"] == "/mounts/my-project"
    mounts.add(str(proj), rw=True, name="code")
    out = mounts.render_override(tmp_path)
    doc = json.loads(out.read_text(encoding="utf-8").split("\n", 1)[1])
    runner = {v["target"]: v["read_only"] for v in doc["services"]["runner"]["volumes"]}
    gateway = {v["target"]: v["read_only"] for v in doc["services"]["gateway"]["volumes"]}
    assert runner == {"/mounts/my-project": True, "/mounts/code": False}
    assert all(gateway.values())
    assert mounts.remove("code") and mounts.remove("my-project")
    assert mounts.render_override(tmp_path) is None and not out.exists()


def test_mount_refuses_home_and_drive(mounts_file):
    with pytest.raises(ValueError):
        mounts.add(str(Path.home()))
    with pytest.raises(ValueError):
        mounts.add(Path.home().anchor)


def test_describe_event():
    assert describe_event({"type": "token", "data": {"text": "x"}}) is None
    style, text = describe_event({"type": "agent", "status": "running", "agent_id": "data_analysis_agent",
                                  "data": {"instruction": "analyze"}})
    assert "data_analysis_agent" in text and style == "cyan"
    _, text = describe_event({"type": "plan", "data": {"plan": {"steps": [
        {"id": "s1", "agent_id": "coding_agent", "instruction": "write"}]}}})
    assert "coding_agent" in text


def test_help_lists_commands():
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("setup", "start", "stop", "doctor", "chat", "tui", "mount", "bridge", "approvals"):
        assert cmd in r.output
