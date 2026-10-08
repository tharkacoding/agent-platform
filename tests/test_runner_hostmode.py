"""Runner host mode (dev, no Docker): container-style paths mapped to host folders, Windows shell selection."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    ws, sh, tmp = tmp_path / "workspace", tmp_path / "shared", tmp_path / "tmp"
    for d in (ws, sh, tmp):
        d.mkdir()
    monkeypatch.setenv("RUNNER_ROOTS", os.pathsep.join(map(str, (ws, sh, tmp))))
    monkeypatch.setenv("RUNNER_PATH_MAP", f"/workspace={ws};/shared={sh};/tmp={tmp}")
    from runner import app as runner_app
    return TestClient(runner_app.app), ws


def test_paths_are_mapped_both_ways(client):
    c, ws = client
    r = c.post("/fs/write", json={"path": "/workspace/proj/a.txt", "content": "hi"}).json()
    assert r["ok"] and r["path"] == "/workspace/proj/a.txt"
    assert (ws / "proj" / "a.txt").read_text() == "hi"
    assert c.get("/fs/read", params={"path": "/workspace/proj/a.txt"}).json()["content"] == "hi"
    entries = c.get("/fs/list", params={"path": "/workspace"}).json()["entries"]
    assert {"path": "/workspace/proj", "type": "dir", "size": 0} in entries
    assert c.get("/fs/read", params={"path": "/etc/passwd"}).status_code == 403


def test_exec_uses_mapped_cwd_and_rewrites_output(client):
    c, ws = client
    h = c.get("/health").json()
    assert h["host_mode"] and h["shell"] in ("bash", "powershell", "sh")
    cmd = "pwd" if h["shell"] in ("bash", "sh") else "(Get-Location).Path"
    r = c.post("/exec", json={"cmd": cmd, "cwd": "/workspace", "timeout": 30}).json()
    assert r["exit_code"] == 0, r
    assert "/workspace" in r["stdout"].replace("\\", "/")


@pytest.mark.skipif(os.name != "nt", reason="Windows host path mapping only")
def test_map_command_rewrites_container_paths(client, monkeypatch):
    from runner import app as runner_app
    out = runner_app.map_command("ls /workspace/proj && cat /shared/x", bash=True)
    assert " /workspace" not in out and " /shared" not in out and out.count(":/") == 2
    assert runner_app.map_command("echo /workspaces", bash=True).endswith("/workspaces")  # no partial match
