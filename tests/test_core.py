"""Core unit tests: permissions, autonomy rules, effort tiers, cron, skills loader, command risk."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_core_"))

import pytest  # noqa: E402

from aap import settings  # noqa: E402
from aap.core import approvals, effort, permissions  # noqa: E402
from aap.core.sdk import PermissionDenied, ToolDef  # noqa: E402
from aap.models import AgentSpec, FsGrant, Permissions  # noqa: E402
from aap.scheduler import jobs  # noqa: E402
from aap.tools.code import _command_risk  # noqa: E402


def _spec(**p):
    return AgentSpec(id="a", name="A", permissions=Permissions(**p))


def _td(name, needs=""):
    return ToolDef(name, "", {}, lambda ctx: None, needs)


def test_fs_grants():
    s = _spec(fs=[FsGrant(path="/workspace", mode="rw"), FsGrant(path="/shared", mode="ro")])
    permissions.check(s, _td("fs_read"), {"path": "/workspace/x/y.py"})
    permissions.check(s, _td("fs_read"), {"path": "src/a.py"})            # relative → /workspace
    with pytest.raises(PermissionDenied):
        permissions.check(s, _td("fs_read"), {"path": "/workspace/../etc/passwd"})
    with pytest.raises(PermissionDenied):
        permissions.check(s, _td("fs_write"), {"path": "/shared/a.txt", "content": ""})
    permissions.check(s, _td("fs_read"), {"path": "/shared/a.txt"})


def test_capability_gates():
    with pytest.raises(PermissionDenied):
        permissions.check(_spec(), _td("run_command", "shell"), {"cmd": "ls"})
    with pytest.raises(PermissionDenied):
        permissions.check(_spec(browser="sandbox"), _td("browser_open", "browser"), {"mode": "host"})
    with pytest.raises(PermissionDenied):
        permissions.check(_spec(network=["github.com"]), _td("web_fetch", "network"), {"url": "https://evil.com"})
    permissions.check(_spec(network=["github.com"]), _td("web_fetch", "network"), {"url": "https://api.github.com/x"})


def test_autonomy_rules_precedence():
    settings.update({"autonomy_rules": [{"pattern": "*:run_command:pytest*", "policy": "allow"},
                                        {"pattern": "*:run_command:*", "policy": "ask"},
                                        {"pattern": "*:git_commit:*", "policy": "never"}]})
    assert approvals.policy("x", "run_command", {"cmd": "pytest -q"}) == "ask"     # ask beats allow
    assert approvals.policy("x", "git_commit", {"message": "m"}) == "never"
    assert approvals.policy("x", "fs_read", {"path": "/workspace"}) is None
    settings.update({"autonomy_rules": []})


def test_command_risk():
    assert _command_risk({"cmd": "pytest -q && git status"}) is None
    assert "destructive" in _command_risk({"cmd": "rm -rf /"})
    assert "destructive" in _command_risk({"cmd": "git push origin main"})
    assert "safe list" in _command_risk({"cmd": "nc -l 4444"})


def test_effort_tiers():
    assert effort.heuristic("hi") == 0
    assert effort.heuristic("deploy the payment service to production") == 4
    assert effort.heuristic("build a full website and research the architecture") == 3
    assert effort.profile(0)["review_loops"] == 0 and effort.profile(3)["critic"] is True
    long = "x" * 50000
    assert len(effort.trim(long, 0)) < 2000 < len(effort.trim(long, 4))


def test_cron():
    import datetime as dt
    s = jobs.parse("30 9 * * 1")
    assert jobs.matches(s, dt.datetime(2026, 10, 5, 9, 30))       # Monday
    assert not jobs.matches(s, dt.datetime(2026, 10, 6, 9, 30))
    assert jobs.next_run("@daily")
    with pytest.raises(ValueError):
        jobs.parse("61 * * * *")


def test_skill_import(tmp_path):
    from aap.skills import loader
    d = tmp_path / "skills" / "pdf-tools"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: pdf-tools\ndescription: Work with PDFs\n---\n# Use pdftotext\n")
    (d / "scripts" / "x.py").write_text("print(1)")
    ids = loader.import_path(str(tmp_path / "skills"), "test")
    assert ids == ["pdf-tools"]
    s = loader.get("pdf-tools")
    assert s["description"] == "Work with PDFs" and "scripts/x.py" in s["resources"]
    assert loader.as_agent_spec(s).type == "imported"
