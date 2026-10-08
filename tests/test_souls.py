"""Souls + workspace persona files: CRUD, identity, tier-sized prompt prefix, /soul command, OpenClaw/Hermes import."""
import os
import tempfile

os.environ["AAP_LLM_PROVIDER"] = "mock"
os.environ.setdefault("AAP_DATA_DIR", tempfile.mkdtemp(prefix="aos_souls_"))

import pytest  # noqa: E402

from aap import events, registry, settings, souls  # noqa: E402

settings.update({"llm_provider": "mock"})


def test_builtins_and_crud():
    ids = [s["id"] for s in souls.list_souls()]
    for want in ("default", "teacher", "engineer", "researcher", "coach", "concise", "friend"):
        assert want in ids
    t = souls.get_soul("teacher")
    assert t["builtin"] and "Socratic" in t["description"] and t["best_for"]
    s = souls.save_soul("pirate", {"name": "Pirate", "icon": "🏴‍☠️", "description": "Talks like a pirate",
                                   "tone": "arr", "best_for": ["fun"], "body": "# Soul: Pirate\n- Say arr."})
    assert s["id"] == "pirate" and s["best_for"] == ["fun"] and "arr" in s["body"]
    souls.save_soul("pirate", {"description": "Updated"})
    assert souls.get_soul("pirate")["description"] == "Updated"
    souls.delete_soul("pirate")
    assert souls.get_soul("pirate") is None
    with pytest.raises(ValueError):
        souls.delete_soul("teacher")          # built-ins: editable, not deletable
    with pytest.raises(ValueError):
        souls.save_soul("Bad Id!", {"name": "x"})


def test_identity_and_workspace_files():
    assert souls.get_identity()["name"] == "AgentOS"
    ident = souls.set_identity("Jarvis", "🤖", "dry wit")
    assert ident == {"name": "Jarvis", "emoji": "🤖", "vibe": "dry wit", "notes": ident["notes"]}
    assert souls.assistant_name() == "Jarvis"
    names = [f["name"] for f in souls.list_files()]
    assert names == ["AGENTS", "USER", "IDENTITY", "TOOLS", "HEARTBEAT", "MEMORY"]
    souls.put_file("USER", "- Name: Rohith\n" + "x" * 9000)
    assert len(souls.get_file("USER")) == souls.USER_MAX and souls.user_name() == "Rohith"
    with pytest.raises(KeyError):
        souls.get_file("SECRETS")
    souls.set_identity("AgentOS", "✨", "calm")


def test_prefix_scales_with_tier():
    souls.put_file("USER", "- Name: Rohith\n- Likes: competitive programming\n")
    low = souls.system_prefix("teacher", 0)
    mid = souls.system_prefix("teacher", 2)
    high = souls.system_prefix("teacher", 4, tools=True)
    assert low.count("\n") <= 2 and "Rohith" in low and "Teacher" in low
    assert len(low) < len(mid) < len(high)
    assert "## Soul" in mid and "Operating rules" in mid and "Tool notes" not in mid
    assert "Tool notes" in high
    assert "<!--" not in high                      # template comments never reach the model


def _main_runtime():
    from aap.core.runtime import Runtime
    import aap.graphs.main  # noqa: F401
    from aap.models import AgentSpec
    registry.init()
    registry.upsert_agent(AgentSpec(id="main_agent", name="Main Agent", type="system", graph="main"))
    return Runtime()


def _chat(rt, msg, cid):
    from aap.models import AgentTask
    run = events.create(registry.new_run("chat", msg), conversation_id=cid)
    return rt.run_agent(AgentTask(task_id="t", agent_id="main_agent", instruction=msg), run)


def test_soul_command_switches_conversation():
    rt = _main_runtime()
    cid = registry.ensure_conversation(None, "hello")
    rep = _chat(rt, "/souls", cid)
    assert "teacher" in rep.result and "/soul <id>" in rep.result
    rep = _chat(rt, "/soul teacher", cid)
    assert "Teacher" in rep.result
    assert registry.get_conversation_soul(cid) == "teacher" and souls.resolve(cid) == "teacher"
    rep = _chat(rt, "/soul nope", cid)
    assert "No soul named" in rep.result
    assert souls.resolve(registry.ensure_conversation(None, "other")) == settings.get("default_soul")


def test_import_openclaw_workspace(tmp_path):
    ws = tmp_path / ".openclaw" / "workspace"
    ws.mkdir(parents=True)
    (ws / "SOUL.md").write_text("# Molty\n- Be lobster-y and helpful.\n", encoding="utf-8")
    (ws / "IDENTITY.md").write_text("# Identity\n- **Name:** Clawd\n- **Emoji:** 🦞\n", encoding="utf-8")
    (ws / "HEARTBEAT.md").write_text("- check inbox\n", encoding="utf-8")
    before = (ws / "SOUL.md").read_text(encoding="utf-8")
    out = souls.import_from(str(ws))
    assert out["kind"] == "openclaw" and out["soul"] == "openclaw-soul"
    assert set(out["files"]) == {"IDENTITY.md", "HEARTBEAT.md"}
    assert "lobster" in souls.get_soul("openclaw-soul")["body"]
    assert souls.get_identity()["name"] == "Clawd"                    # OpenClaw bullet format understood
    assert (ws / "SOUL.md").read_text(encoding="utf-8") == before       # source untouched
    assert (souls.WS_DIR / "IDENTITY.md.bak").exists()                  # our previous file backed up
    souls.set_identity("AgentOS", "✨", "calm")
    with pytest.raises(FileNotFoundError):
        souls.import_from(str(tmp_path / "empty"))


def test_api_routes():
    from fastapi.testclient import TestClient
    from aap.api.app import app
    with TestClient(app) as c:
        assert any(s["id"] == "teacher" for s in c.get("/api/souls").json())
        r = c.post("/api/souls", json={"name": "Night Owl", "icon": "🦉", "description": "late", "body": "# hi"})
        assert r.status_code == 200 and r.json()["id"] == "night-owl"
        assert c.put("/api/souls/night-owl", json={"default": True}).status_code == 200
        assert next(s for s in c.get("/api/souls").json() if s["id"] == "night-owl")["default"]
        assert c.delete("/api/souls/night-owl").json()["ok"] and settings.get("default_soul") == "default"
        assert c.delete("/api/souls/teacher").status_code == 400
        assert c.put("/api/identity", json={"name": "Friday"}).json()["name"] == "Friday"
        assert c.get("/api/health").json()["identity"]["name"] == "Friday"
        assert c.put("/api/workspace-files/TOOLS.md", json={"content": "# tools"}).json()["content"] == "# tools"
        assert len(c.get("/api/workspace-files").json()) == 6
        cid = registry.ensure_conversation(None, "x")
        assert c.put(f"/api/conversations/{cid}/soul", json={"soul": "coach"}).json()["soul"] == "coach"
        assert c.get(f"/api/conversations/{cid}").json()["soul"] == "coach"
        assert c.put(f"/api/conversations/{cid}/soul", json={"soul": "nope"}).status_code == 404
        c.put("/api/identity", json={"name": "AgentOS"})
