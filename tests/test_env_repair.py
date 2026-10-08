"""16.11: a verification that fails because the ENVIRONMENT lacks python/pytest is repaired, not 'debugged'."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_env_"))

from aap.graphs import coding  # noqa: E402


def test_env_problem_detection():
    assert coding.env_problem("bash: line 1: python: command not found") == ("python3", "")
    assert coding.env_problem("/usr/bin/python3: No module named pytest") == ("install", "pytest")
    assert coding.env_problem("/bin/sh: 1: ruff: not found") == ("install", "ruff")
    assert coding.env_problem("ModuleNotFoundError: No module named 'calc'") is None      # a real code bug
    assert coding.env_problem("AssertionError: assert -1 == 5") is None


def test_missing_python_then_pytest_both_repaired(monkeypatch):
    calls = []
    outs = iter([{"ok": False, "output": "bash: python: command not found"},
                 {"ok": False, "output": "/usr/bin/python3: No module named pytest"},
                 {"ok": True, "output": ""},                                 # pip install
                 {"ok": True, "output": "2 passed"}])

    def fake_run(ctx, state, cmd, phase, timeout=300):
        calls.append(cmd)
        return {"cmd": cmd, "phase": phase, "exit_code": 0, **next(outs)}
    monkeypatch.setattr(coding, "_run", fake_run)

    class Ctx:
        def emit(self, *a, **k): pass
    rec = coding._run_repairing(Ctx(), {"repo": "/workspace/x"}, "python -m pytest -q", "test", set())
    assert rec["ok"] and calls[1] == "python3 -m pytest -q" and "pip install -q pytest" in calls[2]
    assert calls[3] == "python3 -m pytest -q"
