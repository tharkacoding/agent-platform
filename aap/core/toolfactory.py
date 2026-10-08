"""Tool Factory: agents create NEW tools for themselves at runtime.

A custom tool = python code defining `run(input: dict) -> dict` + a JSON parameter schema. It is
  1. statically validated (same AST policy as Builder-generated agent code, plus optional network/fs needs),
  2. self-tested in the sandboxed runner with an example input,
  3. approved by the user (they see the code and what it can touch),
  4. saved to data/custom_tools/<name>/{tool.py, manifest.json} and registered in TOOLS immediately
     (and again at every startup) — executed ONLY in the runner, never inside the gateway.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import config
from .sdk import TOOLS, ToolDef

DIR = config.DATA_DIR / "custom_tools"
DIR.mkdir(parents=True, exist_ok=True)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
EXTRA_IMPORTS = {"network": {"urllib", "http", "json", "html"}}


def validate(name: str, code: str, params: dict, needs: str = "") -> list[str]:
    from ..graphs.builder import validate_code
    errs = []
    if not NAME_RE.match(name or ""):
        errs.append("name must be snake_case, 3-41 chars")
    if name in TOOLS and not (DIR / name).exists():
        errs.append(f"'{name}' is a built-in tool")
    if not isinstance(params, dict):
        errs.append("params must be a JSON-schema properties object")
    code_errs = validate_code(code)
    if needs == "network":   # network tools may use urllib (requests go out through the egress proxy in Docker)
        code_errs = [e for e in code_errs if not re.search(r"import not allowed: (urllib|http)", e)]
    return errs + code_errs


def _make_fn(name: str, code: str):
    def fn(ctx, **kwargs):
        from ..clients import ServiceUnavailable, runner
        try:
            r = runner("POST", "/python", json={"code": code, "input": kwargs, "timeout": 60}, timeout=80)
        except ServiceUnavailable as e:
            return {"error": str(e)}
        if isinstance(r, dict) and "output" in r and r.get("ok", True):
            out = r["output"]
            return out if isinstance(out, dict) else {"result": out}
        return r if isinstance(r, dict) else {"error": str(r)}
    fn.__name__ = name
    return fn


def register(manifest: dict, code: str) -> None:
    TOOLS[manifest["name"]] = ToolDef(manifest["name"], "[custom] " + manifest["description"], manifest["params"],
                                      _make_fn(manifest["name"], code), manifest.get("needs", ""))


def save(name: str, description: str, params: dict, code: str, needs: str = "", created_by: str = "",
         test_input: dict | None = None) -> dict:
    d = DIR / name
    d.mkdir(parents=True, exist_ok=True)
    m = {"name": name, "description": description[:300], "params": params, "needs": needs,
         "created_by": created_by, "created_at": time.time(), "test_input": test_input or {}}
    (d / "tool.py").write_text(code, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    register(m, code)
    return m


def load_all() -> int:
    n = 0
    for d in sorted(DIR.iterdir()) if DIR.exists() else []:
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            register(m, (d / "tool.py").read_text(encoding="utf-8"))
            n += 1
        except Exception:  # noqa: BLE001 - one broken tool must not stop startup
            continue
    return n


def list_custom() -> list[dict]:
    out = []
    for d in sorted(DIR.iterdir()) if DIR.exists() else []:
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            m["code"] = (d / "tool.py").read_text(encoding="utf-8")
            out.append(m)
        except Exception:  # noqa: BLE001
            continue
    return out


def delete(name: str) -> bool:
    d = DIR / name
    if not d.exists():
        return False
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    TOOLS.pop(name, None)
    return True


def tools_from(path: Path) -> list[str]:  # pragma: no cover - helper for UI listing
    return [p.name for p in path.iterdir()] if path.exists() else []
