"""Git tools (run inside the runner). Pushing is deliberately not offered as a tool."""
import re
import shlex

from ..clients import ServiceUnavailable, runner
from ..core.sdk import tool


def _git(repo: str, args: str, timeout: int = 60):
    try:
        r = runner("POST", "/exec", json={"cmd": f"git -C {shlex.quote(repo)} {args}", "cwd": repo, "timeout": timeout},
                   timeout=timeout + 15)
    except ServiceUnavailable as e:
        return {"error": str(e)}
    return r


@tool("git_status", "git status (short) + current branch.", {"repo": {"type": "string"}})
def git_status(ctx, repo: str):
    return _git(repo, "status --short --branch")


@tool("git_diff", "Show the git diff (working tree, or staged).",
      {"repo": {"type": "string"}, "staged": {"type": "boolean", "default": False}})
def git_diff(ctx, repo: str, staged: bool = False):
    return _git(repo, "diff --stat --patch" + (" --staged" if staged else ""))


@tool("git_log", "Recent commits.", {"repo": {"type": "string"}, "n": {"type": "integer", "default": 10}})
def git_log(ctx, repo: str, n: int = 10):
    return _git(repo, f"log --oneline -n {int(n)}")


@tool("git_branch", "Create (or switch to) a branch. Initialises the repo if needed.",
      {"repo": {"type": "string"}, "name": {"type": "string"}})
def git_branch(ctx, repo: str, name: str):
    name = re.sub(r"[^A-Za-z0-9._/-]", "-", name)[:60]
    _git(repo, "rev-parse --git-dir || git init -q")
    return _git(repo, f"switch -c {shlex.quote(name)} 2>/dev/null || git -C {shlex.quote(repo)} switch {shlex.quote(name)}")


@tool("git_commit", "Stage all changes and commit them.", {"repo": {"type": "string"}, "message": {"type": "string"}})
def git_commit(ctx, repo: str, message: str):
    msg = shlex.quote(message[:500])
    return _git(repo, f"add -A && git -C {shlex.quote(repo)} -c user.name=AgentOS -c user.email=agentos@local "
                      f"commit -q -m {msg} && git -C {shlex.quote(repo)} log --oneline -n 1")
