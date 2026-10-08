"""TODO N2–N4: single-job tools (chrome_profiles, calc, now, fs_find…), the command crawler + cookbook, tool grants."""
import json
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_everyday_"))

import aap.tools  # noqa: E402,F401 - registers every tool
from aap.core.sdk import TOOLS, UNIVERSAL_TOOLS  # noqa: E402
from aap.tools import commands, everyday  # noqa: E402


class Ctx:
    files_created: list = []

    def llm(self):
        from aap.llm import llm
        return llm


def _fake_chrome(tmp_path):
    ud = tmp_path / "User Data"
    for d in ("Default", "Profile 6"):
        (ud / d).mkdir(parents=True)
    (ud / "Local State").write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Your Chrome", "user_name": "ramanji36@gmail.com"},
        "Profile 6": {"name": "asdf", "user_name": ""}, "Profile 9": {"name": "gone"}}}}), encoding="utf-8")
    return ud


def test_chrome_profiles_reads_local_state_without_the_bridge(tmp_path, monkeypatch):
    ud = _fake_chrome(tmp_path)
    monkeypatch.setenv("AAP_CHROME_USER_DATA", str(ud))
    monkeypatch.setattr(everyday, "bridge", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bridge off")))
    from aap import vmbrowser
    monkeypatch.setattr(vmbrowser, "profiles", lambda refresh=False: {"asdf"})
    out = TOOLS["chrome_profiles"].fn(Ctx())
    names = {p["name"]: p for p in out["profiles"]}
    assert set(names) == {"Your Chrome", "asdf"}                      # folders that no longer exist are dropped
    assert names["asdf"]["dir"] == "Profile 6" and names["asdf"]["vm_logged_in"] is True
    assert names["asdf"]["path"].endswith("Profile 6")
    p = TOOLS["chrome_profile_path"].fn(Ctx(), name="ASDF")
    assert p["dir"] == "Profile 6" and '--profile-directory="Profile 6"' in p["launch"]
    assert "available" in TOOLS["chrome_profile_path"].fn(Ctx(), name="nope")


def test_calc_and_now():
    assert TOOLS["calc"].fn(Ctx(), expression="2*(3+4)")["result"] == 14
    assert TOOLS["calc"].fn(Ctx(), expression="sqrt(16)+2^3")["result"] == 12
    assert "error" in TOOLS["calc"].fn(Ctx(), expression="__import__('os').system('x')")
    n = TOOLS["now"].fn(Ctx())
    assert len(n["date"]) == 10 and n["weekday"]


def test_fs_find_filters_by_pattern(monkeypatch):
    entries = [{"path": "/workspace/a/report.csv", "type": "file", "size": 3},
               {"path": "/workspace/a", "type": "dir", "size": 0},
               {"path": "/workspace/notes.txt", "type": "file", "size": 1}]
    monkeypatch.setattr(everyday, "runner", lambda m, p, **k: {"entries": entries})
    r = TOOLS["fs_find"].fn(Ctx(), pattern="*.csv", path="/workspace")
    assert [f["path"] for f in r["files"]] == ["/workspace/a/report.csv"]
    assert TOOLS["fs_find"].fn(Ctx(), pattern="notes", path="/workspace")["count"] == 1


def test_extract_and_rank_commands():
    page = ("<p>Use this:</p><pre><code>$ chrome.exe --profile-directory=\"Profile 6\" https://x.com\n"
            "for i in range(3):\n</code></pre><code>PS&gt; Start-Process chrome -ArgumentList '--new-window'</code>")
    cmds = commands.extract_commands(page)
    assert 'chrome.exe --profile-directory="Profile 6" https://x.com' in cmds
    assert "Start-Process chrome -ArgumentList '--new-window'" in cmds and not any(c.startswith("for ") for c in cmds)
    ranked = commands.rank("open chrome profile", "chrome",
                           [(c, "s") for c in cmds] + [("sudo apt install chromium", "s")], "windows")
    assert "profile-directory" in ranked[0]["command"] and ranked[-1]["command"].startswith("sudo")


def test_find_command_crawls_saves_then_answers_from_memory(monkeypatch):
    fetched = []
    monkeypatch.setattr(commands, "_search", lambda q, n=8: ["https://superuser.com/q/1", "https://evil.example/x"])

    def fake_fetch(u):
        fetched.append(u)
        return "<pre>chrome.exe --profile-directory=\"Profile 6\" https://leetcode.com</pre>"
    monkeypatch.setattr(commands, "_fetch", fake_fetch)
    r = TOOLS["find_command"].fn(Ctx(), task="open chrome profile at a url", os="windows", app="chrome")
    assert r["from"] == "web" and r["saved"] >= 1 and "profile-directory" in r["best"][0]["command"]
    assert not any("evil.example" in u for u in fetched)               # only command-doc sites are crawled
    monkeypatch.setattr(commands, "_fetch", lambda u: (_ for _ in ()).throw(AssertionError("must not crawl")))
    again = TOOLS["find_command"].fn(Ctx(), task="open chrome profile at a url", os="windows", app="chrome")
    assert again["from"] == "memory" and "profile-directory" in again["commands"][0]
    assert any("profile-directory" in c for c in TOOLS["known_commands"].fn(Ctx(), query="chrome profile")["commands"])


def test_every_agent_gets_the_universal_helpers():
    from aap.agents.prebuilt.catalog import PREBUILT
    for spec in PREBUILT:
        assert set(UNIVERSAL_TOOLS) <= set(spec.tools), spec.id
        if "browser_snapshot" in spec.tools:
            assert "page_text" in spec.tools
    assert all(t in TOOLS for t in UNIVERSAL_TOOLS)


def test_toolcheck_covers_every_tool(monkeypatch):
    from aap import toolcheck
    monkeypatch.setattr(toolcheck, "_one", lambda ctx, n, a, ok: {"tool": n, "status": "ok"})
    monkeypatch.setattr(toolcheck, "_browser", lambda ctx: [])
    out = toolcheck.run(include_web=False)
    listed = {r["tool"] for r in out["results"]}
    assert set(TOOLS) <= listed and out["counts"].get("unchecked", 0) == 0, \
        [r["tool"] for r in out["results"] if r["status"] == "unchecked"]
