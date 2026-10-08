"""Discord channel (DMs with the owner) using discord.py if installed; otherwise disabled with a log line."""
from __future__ import annotations

import asyncio
import logging
import threading

from .. import bus, settings

log = logging.getLogger("aap.discord")


class Discord(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="discord")
        self.owner = int(settings.get("discord_user_id") or 0)
        self.client = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conversation_id: str | None = None

    def send_owner(self, text: str) -> None:
        if not (self.client and self.loop):
            return

        async def _send():
            user = await self.client.fetch_user(self.owner)
            for i in range(0, max(len(text), 1), 1900):
                await user.send(text[i:i + 1900])
        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    def run(self) -> None:
        try:
            import discord  # type: ignore
        except ImportError:
            log.warning("discord.py not installed; Discord channel disabled")
            return
        from ..core.runtime import runtime
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = client = discord.Client(intents=intents)
        from .. import souls
        bus.on(lambda ch, d: ch == "approval" and d.get("status") == "pending" and self.send_owner(
            f"🔐 {souls.assistant_name()} needs your approval ({d['agent_id']}): {d['title']}\n"
            "Approve in the web UI or Telegram."))

        @client.event
        async def on_message(message):
            if message.author.id != self.owner or message.author == client.user:
                return
            def done(run, rep, final):
                self.send_owner(final or rep.summary)
            _, self.conversation_id = runtime.start_chat(message.content, self.conversation_id, kind="discord",
                                                         on_done=done)

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(client.start(settings.get("discord_token")))
