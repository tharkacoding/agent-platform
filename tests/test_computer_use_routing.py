"""Computer-use routing (screen screenshot → desktop, page → browser, commands → terminal) + terminal offline parser."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_cu_"))

import pytest  # noqa: E402

from aap import registry  # noqa: E402
from aap.agents.prebuilt.catalog import PREBUILT  # noqa: E402
from aap.graphs.main import _direct_route  # noqa: E402
from aap.graphs.terminal import offline_commands  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def seeded():
    registry.init()
    for s in PREBUILT:
        registry.upsert_agent(s)


@pytest.mark.parametrize("req,agent", [
    ("take a screenshot of my screen", "desktop_agent"),
    ("what's on my screen? take a screen shot", "desktop_agent"),
    ("open https://news.ycombinator.com in my chrome and take a screenshot", "browser_agent"),
    ("take a screenshot of example.com", "browser_agent"),
    ("run `python --version` and `dir` in the workspace", "terminal_agent"),
    ("create hello.py in the workspace that prints hello, then run it", "terminal_agent"),
    ("list files in /workspace", "terminal_agent"),
    ("pip install requests", "terminal_agent"),
])
def test_direct_routes(req, agent):
    assert _direct_route(req) == agent


@pytest.mark.parametrize("req", ["implement a login feature for my website app",
                                 "research segment trees and summarize", "write an email to my teacher"])
def test_no_direct_route_for_real_work(req):
    assert _direct_route(req) is None


def test_terminal_offline_parser():
    cmds, files = offline_commands("run `python --version` and `dir` in the workspace")
    assert cmds == ["python --version", "dir"] and not files
    cmds, files = offline_commands("create hello.py in the workspace that prints hello, then run it")
    assert files == [("hello.py", "print('hello')\n")] and cmds == ["python hello.py"]
    cmds, _ = offline_commands("please run git status")
    assert cmds == ["git status"]


def test_terminal_and_desktop_agents_in_catalog():
    ids = {s.id: s for s in PREBUILT}
    assert ids["terminal_agent"].permissions.shell and "run_command" in ids["terminal_agent"].tools
    assert "desktop_screenshot" in ids["desktop_agent"].tools
