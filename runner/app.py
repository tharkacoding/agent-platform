"""Runner service — see docs/CONTRACTS.md section 3.

Executes untrusted shell commands, Python snippets and file operations for the Coding Agent and for
Builder-generated agent logic. It runs inside the `runner` container as uid 1000, with no secrets and with
network only through the egress proxy. On top of that, every path is jailed to the allowed roots.

Env:
  RUNNER_ROOTS     ";"-separated allowed roots (Linux: ":" too) (default /workspace:/shared:/mounts:/tmp)
  RUNNER_RO_PATHS  ";"-separated read-only prefixes (writes rejected with 403)
  RUNNER_MAX_OUTPUT bytes kept per stream (default 65536)
  RUNNER_PATH_MAP  host mode (dev, no Docker): "/workspace=C:\\...;/shared=C:\\...;/tmp=C:\\..." — agents keep
                   using container-style paths; they are mapped to host folders on the way in and back on the way out
  RUNNER_SHELL     auto | bash | powershell | sh (Windows auto: Git Bash if installed, else PowerShell)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="AgentOS runner", version="1.0")

IS_WIN = os.name == "nt"
RESULT_MARKER = "__AOS_RESULT__"


# ---------------------------------------------------------------------------------------------- config
def _paths(env: str, default: str) -> list[Path]:
    """';' always separates (like RUNNER_PATH_MAP); on Linux ':' too. The VM's scripts/vm_up.sh passes
    'a;b;c' — split only on os.pathsep (':') that became ONE bogus root and every /tmp path was refused."""
    raw = os.environ.get(env, default)
    parts = raw.split(";") if os.name == "nt" else re.split(r"[;:]", raw)
    return [Path(p).resolve() for p in parts if p.strip()]


def roots() -> list[Path]:
    return _paths("RUNNER_ROOTS", os.pathsep.join(["/workspace", "/shared", "/mounts", "/tmp"]))


def ro_paths() -> list[Path]:
    return _paths("RUNNER_RO_PATHS", "")


def max_output() -> int:
    return int(os.environ.get("RUNNER_MAX_OUTPUT", "65536"))


# ---------------------------------------------------------------------------------------------- host path map
def path_map() -> list[tuple[str, str]]:
    """[(container_prefix, host_dir)] longest prefix first. Empty in Docker."""
    raw = os.environ.get("RUNNER_PATH_MAP", "")
    out = []
    for part in raw.split(";"):
        if "=" in part:
            c, h = part.split("=", 1)
            c, h = c.strip().rstrip("/"), h.strip()
            if c.startswith("/") and h:
                out.append((c, str(Path(h).resolve())))
    return sorted(out, key=lambda x: -len(x[0]))


def to_host(path: str) -> str:
    p = path.replace("\\", "/") if path.startswith("/") else path
    for c, h in path_map():
        if p == c or p.startswith(c + "/"):
            return h + p[len(c):].replace("/", os.sep)
    return path


def to_container(text: str) -> str:
    """Rewrite host paths in output back to container-style paths (both slash styles, case-insensitive)."""
    if not text:
        return text
    for c, h in path_map():
        for variant in {h, h.replace("\\", "/")}:
            text = re.sub(re.escape(variant) + r"(?P<rest>[\\/][^\s'\"<>|:]*)?",
                          lambda m: c + (m.group("rest") or "").replace("\\", "/"), text, flags=re.I)
    return text


def map_command(cmd: str, bash: bool) -> str:
    """Replace container-style path tokens inside a shell command with host paths."""
    for c, h in path_map():
        host = h.replace("\\", "/") if bash else h
        cmd = re.sub(r"(?<![\w./-])" + re.escape(c) + r"(?=/|\b|$)", lambda _m: host, cmd)
    return cmd


def shell_kind() -> str:
    pref = os.environ.get("RUNNER_SHELL", "auto")
    if pref != "auto":
        return pref
    if not IS_WIN:
        return "sh"
    return "bash" if _git_bash() else "powershell"


def _git_bash() -> str:
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(p).exists():
            return p
    return ""


def shell_args(cmd: str) -> tuple[list[str] | str, bool]:
    """Returns (args, shell=) for subprocess."""
    kind = shell_kind()
    if kind == "bash" and IS_WIN:
        return [_git_bash() or "bash", "-c", map_command(cmd, True)], False
    if kind == "powershell":
        exe = shutil.which("pwsh") or "powershell.exe"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", map_command(cmd, False)], False
    return map_command(cmd, True), True


def _under(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


def jail(path: str, write: bool = False) -> Path:
    """Resolve `path` (relative paths are relative to the first root) and enforce the jail."""
    if not path:
        raise HTTPException(400, "path required")
    rs = roots()
    p = Path(to_host(path))
    if not p.is_absolute():
        p = rs[0] / p
    p = p.resolve()  # resolves symlinks and '..'
    if not any(_under(p, r) for r in rs):
        raise HTTPException(403, f"path outside allowed roots: {path}")
    if write and any(_under(p, r) for r in ro_paths()):
        raise HTTPException(403, f"path is read-only: {path}")
    return p


def _op(p) -> str:
    """Path for output: container-style when a host path map is active."""
    return to_container(str(p)).replace("\\", "/")


def _cap(b: bytes | str) -> str:
    s = b.decode("utf-8", "replace") if isinstance(b, bytes) else b
    s = to_container(s)
    n = max_output()
    return s if len(s) <= n else s[:n] + f"\n… [truncated {len(s) - n} chars]"


# ---------------------------------------------------------------------------------------------- process helpers
def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _run(args, *, shell: bool, cwd: Path, timeout: float, env: dict | None = None,
         stdin: Optional[bytes] = None) -> dict:
    full_env = dict(os.environ)
    for k in ("RUNNER_ROOTS", "RUNNER_RO_PATHS", "RUNNER_PATH_MAP", "RUNNER_SHELL"):
        full_env.pop(k, None)
    for k in [k for k in full_env if k.startswith(("AAP_", "OPENAI", "ANTHROPIC", "BRIDGE_"))]:
        full_env.pop(k, None)   # never leak gateway secrets/config into agent commands
    full_env.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    kw: dict[str, Any] = {}
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    t = time.time()
    proc = subprocess.Popen(args, shell=shell, cwd=str(cwd), env=full_env,
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out, err = b"", b""
    return {"exit_code": proc.returncode if not timed_out else -9, "stdout": out or b"", "stderr": err or b"",
            "duration_s": round(time.time() - t, 3), "timed_out": timed_out}


# ---------------------------------------------------------------------------------------------- models
class ExecReq(BaseModel):
    cmd: str
    cwd: str = ""
    timeout: int = 120
    env: dict[str, str] | None = None


class PythonReq(BaseModel):
    code: str
    input: Any = None
    timeout: int = 60
    path_keys: list[str] = []   # input keys holding file paths: mapped to real paths + jailed before the code runs


class WriteReq(BaseModel):
    path: str
    content: str


class PatchReq(BaseModel):
    path: str
    old: str
    new: str
    count: int = 1     # exact number of occurrences expected; 0 = replace all


class SearchReq(BaseModel):
    pattern: str
    path: str = ""
    glob: str = ""
    max: int = 200


# ---------------------------------------------------------------------------------------------- routes
@app.get("/health")
def health() -> dict:
    return {"ok": True, "roots": [to_container(str(r)) for r in roots()], "rg": bool(shutil.which("rg")),
            "shell": shell_kind(), "host_mode": bool(path_map()),
            "path_map": {c: h for c, h in path_map()}}


@app.post("/exec")
def exec_cmd(req: ExecReq) -> dict:
    cwd = jail(req.cwd or str(roots()[0]))
    if not cwd.is_dir():
        if cwd.parent.is_dir() and any(_under(cwd, r) for r in roots()):
            cwd.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(404, f"cwd not found: {req.cwd}")
    timeout = max(1, min(req.timeout, 1800))
    args, use_shell = shell_args(req.cmd)
    r = _run(args, shell=use_shell, cwd=cwd, timeout=timeout, env=req.env)
    r["shell"] = shell_kind()
    r["stdout"], r["stderr"] = _cap(r["stdout"]), _cap(r["stderr"])
    return r


_PY_WRAPPER = r'''
import json, sys, traceback
_src = open(sys.argv[1], encoding="utf-8").read()
_inp = json.loads(sys.stdin.read() or "null")
_ns = {"__name__": "__aos_main__"}
try:
    exec(compile(_src, "agent_code.py", "exec"), _ns)
    if callable(_ns.get("run")):
        _out = _ns["run"](_inp)
        _payload = {"ok": True, "output": _out}
    else:   # no run(): a top-level `output` / `result` variable is the result (what agents naturally write)
        _payload = {"ok": True, "output": _ns.get("output", _ns.get("result"))}
except Exception:
    _payload = {"ok": False, "error": traceback.format_exc(limit=8)}
try:
    _s = json.dumps(_payload, default=str)
except Exception as e:
    _s = json.dumps({"ok": False, "error": "result not JSON serialisable: %s" % e})
sys.stdout.write("\n__AOS_RESULT__" + _s + "\n")
'''


@app.post("/python")
def run_python(req: PythonReq) -> dict:
    timeout = max(1, min(req.timeout, 600))
    if req.path_keys and isinstance(req.input, dict):   # host mode: '/workspace/x.csv' must reach pandas as the real path
        req.input = {k: (str(jail(v)) if k in req.path_keys and isinstance(v, str) and v.startswith("/")
                         and "\n" not in v else v) for k, v in req.input.items()}
    work = Path(tempfile.mkdtemp(prefix="aos_py_", dir=str(_tmp_root())))
    try:
        (work / "agent_code.py").write_text(req.code, encoding="utf-8")
        (work / "wrapper.py").write_text(_PY_WRAPPER, encoding="utf-8")
        r = _run([sys.executable, "-I", str(work / "wrapper.py"), str(work / "agent_code.py")], shell=False, cwd=work,
                 timeout=timeout, stdin=json.dumps(req.input, default=str).encode())
        stdout = r["stdout"].decode("utf-8", "replace")
        stderr = _cap(r["stderr"])
        if r["timed_out"]:
            return {"ok": False, "output": None, "error": f"timed out after {timeout}s", "stdout": _cap(stdout),
                    "stderr": stderr}
        idx = stdout.rfind(RESULT_MARKER)
        if idx == -1:
            return {"ok": False, "output": None, "error": f"process exited with code {r['exit_code']} without a result",
                    "stdout": _cap(stdout), "stderr": stderr}
        payload = json.loads(stdout[idx + len(RESULT_MARKER):].strip())
        user_out = stdout[:idx].rstrip("\n")
        return {"ok": payload.get("ok", False), "output": payload.get("output"), "error": payload.get("error"),
                "stdout": _cap(user_out), "stderr": stderr}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _tmp_root() -> Path:
    for r in roots():
        if r.name == "tmp" or str(r).endswith(("/tmp", "\\tmp")):
            r.mkdir(parents=True, exist_ok=True)
            return r
    return Path(tempfile.gettempdir())


@app.get("/fs/list")
def fs_list(path: str = Query(""), depth: int = Query(2)) -> dict:
    base = jail(path or str(roots()[0]))
    if not base.exists():
        raise HTTPException(404, f"not found: {path}")
    depth = max(0, min(depth, 8))
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}
    entries: list[dict] = []

    def walk(d: Path, level: int) -> None:
        try:
            items = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for it in items:
            if len(entries) >= 2000:
                return
            is_dir = it.is_dir()
            entries.append({"path": _op(it), "type": "dir" if is_dir else "file",
                            "size": 0 if is_dir else it.stat().st_size})
            if is_dir and level < depth and it.name not in skip:
                walk(it, level + 1)

    if base.is_file():
        entries.append({"path": _op(base), "type": "file", "size": base.stat().st_size})
    else:
        walk(base, 1)
    return {"entries": entries, "truncated": len(entries) >= 2000}


@app.get("/fs/realpath")
def fs_realpath(path: str = Query(...)) -> dict:
    """Where an agent path (/workspace/site/index.html) really is — so the browser can open it as file://."""
    p = jail(path)
    if not p.exists():
        raise HTTPException(404, f"not found: {path}")
    return {"path": _op(p), "real": str(p), "file_url": p.as_uri()}


@app.get("/fs/read")
def fs_read(path: str, start: int = 0, end: int = 0) -> dict:
    p = jail(path)
    if not p.is_file():
        raise HTTPException(404, f"not a file: {path}")
    if p.stat().st_size > 5_000_000:
        raise HTTPException(413, "file larger than 5 MB")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if start or end:
        s = max(1, start or 1)
        e = end if end and end >= s else total
        text = "".join(lines[s - 1:e])
    return {"path": _op(p), "content": _cap(text), "lines": total}


@app.post("/fs/write")
def fs_write(req: WriteReq) -> dict:
    p = jail(req.path, write=True)
    created = not p.exists()
    if p.is_dir():
        raise HTTPException(400, "path is a directory")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content, encoding="utf-8", newline="")
    return {"ok": True, "created": created, "path": _op(p)}


class WriteB64Req(BaseModel):
    path: str
    data_b64: str


@app.post("/fs/write_b64")
def fs_write_b64(req: WriteB64Req) -> dict:
    """Binary write (screenshots, downloads). Same jail as /fs/write; max 20 MB."""
    import base64
    p = jail(req.path, write=True)
    raw = base64.b64decode(req.data_b64)
    if len(raw) > 20_000_000:
        raise HTTPException(413, "file larger than 20 MB")
    created = not p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return {"ok": True, "created": created, "path": _op(p), "bytes": len(raw)}


@app.post("/fs/patch")
def fs_patch(req: PatchReq) -> dict:
    p = jail(req.path, write=True)
    if not p.is_file():
        raise HTTPException(404, f"not a file: {req.path}")
    if not req.old:
        raise HTTPException(400, "old must be non-empty")
    text = p.read_text(encoding="utf-8")
    found = text.count(req.old)
    if found == 0:
        raise HTTPException(409, "old text not found")
    if req.count and found != req.count:
        raise HTTPException(409, f"expected {req.count} occurrence(s) of old text, found {found}; add more context")
    new_text = text.replace(req.old, req.new) if not req.count else text.replace(req.old, req.new, req.count)
    p.write_text(new_text, encoding="utf-8", newline="")
    return {"ok": True, "replacements": found if not req.count else req.count}


@app.post("/search")
def search(req: SearchReq) -> dict:
    base = jail(req.path or str(roots()[0]))
    limit = max(1, min(req.max, 2000))
    rg = shutil.which("rg")
    if rg:
        args = [rg, "--json", "-m", str(limit), "--max-columns", "400"]
        if req.glob:
            args += ["-g", req.glob]
        args += ["-e", req.pattern, str(base)]
        r = _run(args, shell=False, cwd=base if base.is_dir() else base.parent, timeout=60)
        matches = []
        for line in r["stdout"].decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "match":
                continue
            d = ev["data"]
            matches.append({"path": _op(d["path"].get("text", "")), "line": d["line_number"],
                            "text": d["lines"].get("text", "").rstrip("\n")[:400]})
            if len(matches) >= limit:
                break
        return {"matches": matches, "engine": "rg"}
    # pure-python fallback
    try:
        rx = re.compile(req.pattern)
    except re.error as e:
        raise HTTPException(400, f"bad regex: {e}")
    import fnmatch
    matches = []
    files = [base] if base.is_file() else (f for f in base.rglob("*") if f.is_file())
    for f in files:
        if any(part in {".git", "node_modules", "__pycache__"} for part in f.parts):
            continue
        if req.glob and not fnmatch.fnmatch(f.name, req.glob):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": _op(f), "line": i, "text": line[:400]})
                    if len(matches) >= limit:
                        return {"matches": matches, "engine": "python"}
        except OSError:
            continue
    return {"matches": matches, "engine": "python"}
