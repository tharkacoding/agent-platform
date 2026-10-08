"""Host mode (runner on this machine, no Docker): command and Python approval policy."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_host_"))

import pytest  # noqa: E402

from aap import config  # noqa: E402
from aap.tools import code  # noqa: E402


@pytest.fixture()
def host(monkeypatch):
    monkeypatch.setattr(config, "RUNNER_ON_HOST", True)


@pytest.mark.parametrize("cmd", ["ls -la", "dir", "git status", "git log --oneline -n 5", "python --version",
                                 "cat README.md | head -n 5", "pwd && ls", "rg TODO"])
def test_readonly_commands_auto_allowed(host, cmd):
    assert code._command_risk({"cmd": cmd}) is None


@pytest.mark.parametrize("cmd", ["python hello.py", "pip install requests", "npm install", "git commit -m x",
                                 "echo hi > a.txt", "mkdir x", "find . -delete", "ls $(rm x)", "node app.js"])
def test_everything_else_needs_approval(host, cmd):
    r = code._command_risk({"cmd": cmd})
    assert r and "host mode" in r


def test_destructive_still_flagged_as_destructive(host):
    assert "destructive" in code._command_risk({"cmd": "rm -rf /"})


def test_python_policy(host):
    assert code._python_risk({"code": "def run(input):\n    return {'n': len(str(input))}\n"}) is None
    assert "host mode" in code._python_risk({"code": "import os\nos.remove('x')"})


def test_container_mode_unchanged(monkeypatch):
    monkeypatch.setattr(config, "RUNNER_ON_HOST", False)
    assert code._command_risk({"cmd": "python hello.py"}) is None
    assert code._python_risk({"code": "import os"}) is None
