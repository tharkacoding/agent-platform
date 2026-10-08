"""Runner service tests. Allowed roots point at tmp dirs so this runs on Windows and Linux."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ws, ro, tmp, outside = tmp_path / "workspace", tmp_path / "ro", tmp_path / "tmp", tmp_path / "outside"
    for d in (ws, ro, tmp, outside):
        d.mkdir()
    (ro / "keep.txt").write_text("read only", encoding="utf-8")
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setenv("RUNNER_ROOTS", os.pathsep.join(map(str, (ws, ro, tmp))))
    monkeypatch.setenv("RUNNER_RO_PATHS", str(ro))
    from runner.app import app
    return TestClient(app), ws, ro, outside


def test_health(env):
    c, *_ = env
    assert c.get("/health").json()["ok"] is True


def test_write_read_list(env):
    c, ws, *_ = env
    r = c.post("/fs/write", json={"path": str(ws / "a/b.txt"), "content": "l1\nl2\nl3\n"})
    assert r.status_code == 200 and r.json()["created"] is True
    r = c.get("/fs/read", params={"path": str(ws / "a/b.txt"), "start": 2, "end": 3})
    assert r.json()["content"] == "l2\nl3\n" and r.json()["lines"] == 3
    # relative paths resolve against the first root
    assert c.get("/fs/read", params={"path": "a/b.txt"}).status_code == 200
    names = [e["path"].rsplit("/", 1)[-1] for e in c.get("/fs/list", params={"path": str(ws)}).json()["entries"]]
    assert "a" in names and "b.txt" in names


def test_path_traversal_rejected(env):
    c, ws, _, outside = env
    assert c.get("/fs/read", params={"path": str(ws / ".." / "outside" / "secret.txt")}).status_code == 403
    assert c.get("/fs/read", params={"path": str(outside / "secret.txt")}).status_code == 403
    assert c.post("/fs/write", json={"path": "../outside/x.txt", "content": "x"}).status_code == 403
    assert c.post("/exec", json={"cmd": "echo hi", "cwd": str(outside)}).status_code == 403


def test_readonly_write_rejected(env):
    c, _, ro, _ = env
    assert c.get("/fs/read", params={"path": str(ro / "keep.txt")}).status_code == 200
    assert c.post("/fs/write", json={"path": str(ro / "keep.txt"), "content": "x"}).status_code == 403
    assert c.post("/fs/patch", json={"path": str(ro / "keep.txt"), "old": "read", "new": "x"}).status_code == 403


def test_patch(env):
    c, ws, *_ = env
    f = ws / "p.py"
    f.write_text("x = 1\ny = 1\n", encoding="utf-8")
    assert c.post("/fs/patch", json={"path": str(f), "old": "x = 1", "new": "x = 2"}).json()["replacements"] == 1
    assert f.read_text(encoding="utf-8") == "x = 2\ny = 1\n"
    assert c.post("/fs/patch", json={"path": str(f), "old": "zzz", "new": "q"}).status_code == 409
    assert c.post("/fs/patch", json={"path": str(f), "old": "= ", "new": "=", "count": 1}).status_code == 409  # ambiguous
    assert c.post("/fs/patch", json={"path": str(f), "old": "= ", "new": "=", "count": 0}).json()["replacements"] == 2


def test_exec_ok_and_timeout(env):
    c, ws, *_ = env
    r = c.post("/exec", json={"cmd": f'"{sys.executable}" -c "print(6*7)"', "cwd": str(ws)}).json()
    assert r["exit_code"] == 0 and "42" in r["stdout"] and not r["timed_out"]
    r = c.post("/exec", json={"cmd": f'"{sys.executable}" -c "import time; time.sleep(30)"', "cwd": str(ws),
                              "timeout": 2}).json()
    assert r["timed_out"] is True and r["duration_s"] < 15


def test_python_run(env):
    c, *_ = env
    code = "def run(inp):\n    print('side output')\n    return {'sum': sum(inp['nums'])}\n"
    r = c.post("/python", json={"code": code, "input": {"nums": [1, 2, 3]}}).json()
    assert r["ok"] is True and r["output"] == {"sum": 6} and "side output" in r["stdout"]
    r = c.post("/python", json={"code": "def run(i):\n    raise ValueError('boom')\n"}).json()
    assert r["ok"] is False and "boom" in r["error"]
    r = c.post("/python", json={"code": "import time\ndef run(i):\n    time.sleep(30)\n", "timeout": 2}).json()
    assert r["ok"] is False and "timed out" in r["error"]


def test_search(env):
    c, ws, *_ = env
    (ws / "m.py").write_text("def hello():\n    return 'needle'\n", encoding="utf-8")
    r = c.post("/search", json={"pattern": "needle", "path": str(ws)}).json()
    assert len(r["matches"]) == 1 and r["matches"][0]["line"] == 2
    r = c.post("/search", json={"pattern": "needle", "path": str(ws), "glob": "*.txt"}).json()
    assert r["matches"] == []
