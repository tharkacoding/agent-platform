"""TODO P1/P2: LangSmith tracing is OFF by default, switches on with key + switch, wraps the OpenAI client, names runs."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_trace_"))

from aap import settings, tracing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class _Spec:
    id, graph = "browser_agent", "browser"


class _Task:
    task_id, effort, instruction, parent_agent, depth = "s1", 2, "open leetcode " * 50, "main_agent", 1


class _Run:
    run_id = "r123"


def test_off_by_default_changes_nothing():
    settings.update({"langsmith_enabled": False, "langsmith_api_key": ""})
    st = tracing.apply()
    assert st["active"] is False and os.environ.get("LANGSMITH_TRACING") == "false"
    assert tracing.run_config(_Spec(), _Task(), _Run()) == {}
    sentinel = object()
    assert tracing.wrap_client(sentinel) is sentinel


def test_enabled_needs_key_and_sets_env_and_names_runs():
    settings.update({"langsmith_enabled": True, "langsmith_api_key": ""})
    assert tracing.apply()["active"] is False                       # switch alone is not enough
    settings.update({"langsmith_enabled": True, "langsmith_api_key": "lsv2_test_key", "langsmith_project": "agentos-t"})
    st = tracing.apply()
    assert st["active"] and st["project"] == "agentos-t"
    assert os.environ["LANGSMITH_TRACING"] == "true" and os.environ["LANGSMITH_PROJECT"] == "agentos-t"
    cfg = tracing.run_config(_Spec(), _Task(), _Run())
    assert cfg["run_name"] == "browser_agent" and "agent:browser_agent" in cfg["tags"] and "tier:2" in cfg["tags"]
    assert cfg["metadata"]["run_id"] == "r123" and len(cfg["metadata"]["request"]) <= 300
    from openai import OpenAI
    wrapped = tracing.wrap_client(OpenAI(base_url="http://localhost:9", api_key="x"))
    assert "langsmith" in type(wrapped.chat.completions.create).__module__ or hasattr(wrapped.chat.completions.create, "__wrapped__")
    settings.update({"langsmith_enabled": False})
    assert tracing.apply()["active"] is False and "LANGSMITH_API_KEY" not in os.environ


def test_api_key_is_a_masked_secret():
    settings.update({"langsmith_api_key": "lsv2_secret_value_123"})
    shown = settings.all_settings()["langsmith_api_key"]
    assert "secret" not in shown and shown
    settings.update({"langsmith_api_key": shown})                   # the UI echoes the mask back: keep the real key
    assert settings.get("langsmith_api_key") == "lsv2_secret_value_123"
    settings.update({"langsmith_api_key": "", "langsmith_enabled": False})


def test_agent_inventory_is_generated_from_the_real_code(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_agent_docs.py")], capture_output=True, text=True,
                       timeout=180, cwd=ROOT, env=dict(os.environ, AAP_DATA_DIR=str(tmp_path)))
    assert r.returncode == 0, r.stderr[-400:]
    doc = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")
    for needle in ("main_agent", "browser_agent", "competitive_programming_agent", "```mermaid", "aap/graphs/browser.py",
                   "LangSmith"):
        assert needle in doc, needle


def test_prompt_hub_text_extraction_and_fallbacks(monkeypatch):
    from langchain_core.messages import SystemMessage
    from langchain_core.prompts import ChatPromptTemplate
    assert tracing._text_of(ChatPromptTemplate.from_messages([SystemMessage(content="You are {x} careful")])) == \
        "You are {x} careful"                                      # message objects: braces stay braces
    assert tracing._text_of(ChatPromptTemplate.from_messages([("system", "Be brief")])) == "Be brief"
    assert tracing.hub_name("web_research_agent") == "agentos-web-research-agent"
    tracing._prompt_cache.clear()
    settings.update({"langsmith_enabled": False, "langsmith_api_key": "", "langsmith_prompts": True})
    tracing.apply()
    assert tracing.agent_prompt(_Spec()) == ""                      # tracing off → the code's prompt is used
    settings.update({"langsmith_enabled": True, "langsmith_api_key": "lsv2_x", "langsmith_prompts": True})
    tracing.apply()

    class Cli:
        def pull_prompt(self, name):
            assert name == "agentos-browser-agent"
            return ChatPromptTemplate.from_messages([SystemMessage(content="EDITED IN LANGSMITH")])
    monkeypatch.setattr(tracing, "_client", lambda: Cli())
    assert tracing.agent_prompt(_Spec()) == "EDITED IN LANGSMITH"
    from aap.graphs import _common as c
    from aap.agents.prebuilt.catalog import PREBUILT

    class Ctx:
        spec = next(a for a in PREBUILT if a.id == "browser_agent")
        class run: pass
    tracing._prompt_cache.clear()
    assert "EDITED IN LANGSMITH" in c.system(Ctx(), "role")
    monkeypatch.setattr(tracing, "_client", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    tracing._prompt_cache.clear()
    assert tracing.agent_prompt(_Spec()) == ""                      # hub unreachable → code prompt, no crash
    settings.update({"langsmith_enabled": False, "langsmith_api_key": "", "langsmith_prompts": False})
    tracing.apply()


def test_push_prompts_uses_message_objects(monkeypatch):
    settings.update({"langsmith_enabled": True, "langsmith_api_key": "lsv2_x"})
    tracing.apply()
    pushed = {}

    class Cli:
        def push_prompt(self, name, object=None, description="", is_public=False):
            pushed[name] = (object.messages[0].content, is_public)
    monkeypatch.setattr(tracing, "_client", lambda: Cli())
    r = tracing.push_prompts()
    assert r["ok"] and r["pushed"] >= 30 and "agentos-main-agent" in pushed and pushed["agentos-main-agent"][1] is False
    settings.update({"langsmith_enabled": False, "langsmith_api_key": ""})
    tracing.apply()
