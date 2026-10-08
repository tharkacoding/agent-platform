"""Code execution tools — everything runs in the sandboxed runner container, never in the gateway."""
import re
from pathlib import Path

from ..clients import ServiceUnavailable, runner
from ..core.sdk import tool

SAFE_PREFIXES = ("ls", "cat", "pwd", "echo", "python", "python3", "pip", "pytest", "node", "npm", "npx", "pnpm",
                 "yarn", "git status", "git diff", "git log", "git add", "git commit", "git checkout", "git switch",
                 "git branch", "git init", "git show", "rg", "grep", "find", "mkdir", "touch", "cp", "mv", "head",
                 "tail", "wc", "sort", "uniq", "make", "cargo", "go ", "javac", "java", "g++", "gcc", "clang",
                 "tsc", "ruff", "black", "eslint", "prettier", "vite", "uv ", "poetry", "sed -n", "diff", "tree",
                 "cd ")
DANGEROUS = [r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\*)", r"\bgit\s+push\b", r"\bsudo\b", r"\bmkfs", r"\bdd\s+if=",
             r"curl[^|]*\|\s*(ba)?sh", r"wget[^|]*\|\s*(ba)?sh", r"chmod\s+-R\s+777", r":\(\)\s*\{", r"\bshutdown\b",
             r"\breboot\b", r">\s*/dev/sd", r"\bgit\s+reset\s+--hard\b", r"\bgit\s+clean\s+-[a-z]*f"]


# Host mode (no container): only clearly read-only commands run without asking.
READONLY_HOST = re.compile(
    r"^(ls|dir|cat|type|echo|pwd|cd|head|tail|wc|tree|which|where|whoami|date|"
    r"git (status|diff|log|show|branch( -[avr]+)?$|rev-parse|remote -v)|rg|grep|"
    r"find(?!.*(-delete|-exec))|python3? --version|python3? -V|node --version|node -v|npm --version|npm ls|"
    r"pip list|pip show|pip --version|get-childitem|get-content|get-location|get-date)\b", re.I)


def _host_mode() -> bool:
    from .. import config
    return config.RUNNER_ON_HOST


def _command_risk(args: dict):
    cmd = (args.get("cmd") or "").strip()
    for pat in DANGEROUS:
        if re.search(pat, cmd):
            return f"potentially destructive command: {cmd[:200]}"
    parts = [p.strip() for p in re.split(r"&&|\|\||;|\|", cmd) if p.strip()]
    if _host_mode():
        if re.search(r"(^|[^>])>{1,2}[^&]|\$\(|`", cmd):          # redirection / substitution can write anything
            return f"host mode (no sandbox): {cmd[:200]}"
        for p in parts:
            if not READONLY_HOST.match(p):
                return f"host mode (no sandbox): {p[:200]}"
        return None
    for p in parts:
        if not p.startswith(SAFE_PREFIXES):
            return f"command not in the safe list: {p[:200]}"
    return None


def _python_risk(args: dict):
    """Host mode: arbitrary Python needs approval unless it passes the pure-logic AST policy (Builder tests)."""
    if not _host_mode():
        return None
    try:
        from ..graphs.builder import validate_code
        if not validate_code(args.get("code") or ""):
            return None
    except Exception:  # noqa: BLE001
        pass
    return "host mode (no sandbox): run Python code on this computer"


def _safe(fn):
    def wrap(ctx, **kw):
        try:
            return fn(ctx, **kw)
        except ServiceUnavailable as e:
            return {"error": str(e)}
    return wrap


@tool("run_command", "Run a shell command in the sandbox (bash). Returns exit_code/stdout/stderr.",
      {"cmd": {"type": "string"}, "cwd": {"type": "string", "default": "/workspace"},
       "timeout": {"type": "integer", "default": 120}}, needs="shell", risky=_command_risk)
@_safe
def run_command(ctx, cmd: str, cwd: str = "/workspace", timeout: int = 120):
    return runner("POST", "/exec", json={"cmd": cmd, "cwd": cwd, "timeout": timeout}, timeout=timeout + 15)


@tool("run_python", "Run Python code in an isolated sandbox process. If it defines run(input) it is called with "
      "`input` and its JSON-serialisable return value is the output.",
      {"code": {"type": "string"}, "input": {"type": "object", "default": None},
       "timeout": {"type": "integer", "default": 60}}, risky=_python_risk)
@_safe
def run_python(ctx, code: str, input=None, timeout: int = 60):
    return runner("POST", "/python", json={"code": code, "input": input, "timeout": timeout}, timeout=timeout + 15)


@tool("search_code", "Search file contents with ripgrep (regex). Returns path/line/text matches.",
      {"pattern": {"type": "string"}, "path": {"type": "string", "default": "/workspace"},
       "glob": {"type": "string", "default": ""}})
@_safe
def search_code(ctx, pattern: str, path: str = "/workspace", glob: str = ""):
    return runner("POST", "/search", json={"pattern": pattern, "path": path, "glob": glob or "", "max": 200})


@tool("custom_logic", "Run this agent's own generated logic.py `run(input)` in the sandbox.",
      {"input": {"type": "object"}})
@_safe
def custom_logic(ctx, input: dict):
    loc = ctx.spec.location
    p = Path(loc) / "logic.py" if loc else None
    if not p or not p.exists():
        return {"error": "this agent has no logic.py"}
    return runner("POST", "/python", json={"code": p.read_text(encoding="utf-8"), "input": input, "timeout": 60},
                  timeout=80)
