"""Headless TUI smoke test with a fake gateway (Textual pilot)."""
from __future__ import annotations

import asyncio

from cli import tui as tui_mod


class FakeGateway:
    def __init__(self):
        self.decisions = []

    def health(self):
        return {"llm": {"mode": "mock", "model": "m"}, "services": {"runner": True}}

    def agents(self):
        return [{"id": "main_agent", "name": "Main Agent", "type": "system", "icon": "🧠", "category": "System"},
                {"id": "coding_agent", "name": "Coding Agent", "type": "prebuilt", "icon": "🧑‍💻", "category": "Programming"}]

    def approvals(self, status="pending"):
        return [{"id": "ap1", "title": "run_command: rm -rf build", "agent_id": "coding_agent"}]

    def decide(self, aid, decision):
        self.decisions.append((aid, decision))
        return {"ok": True}

    def chat(self, message, conversation_id=None):
        return {"run_id": "r1", "conversation_id": "c1"}

    def stream_events(self, run_id):
        yield {"type": "agent", "status": "running", "agent_id": "coding_agent", "data": {"instruction": "do it"}}
        yield {"type": "final", "data": {"text": "**done**"}}


def test_tui_chat_and_approve():
    async def run():
        app = tui_mod.AgentOSApp()
        fake = FakeGateway()
        app.gw = fake
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.5)
            assert len(app.query_one("#agent_list").children) == 2
            app.query_one("#prompt").value = "hello"
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause(0.1)
                if app.busy_since is None and app.conversation_id == "c1":
                    break
            assert app.conversation_id == "c1"
            approvals = app.query_one("#approvals")
            approvals.focus()
            approvals.index = 0
            await pilot.press("a")
            await pilot.pause(0.5)
            assert ("ap1", "approve") in fake.decisions

    asyncio.run(run())
