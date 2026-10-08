"""Chat channels registry. Channels receive user messages -> Main Agent, and get notified of approvals/schedules."""
from __future__ import annotations

import logging

log = logging.getLogger("aap.channels")
_channels: list = []


def register(ch) -> None:
    _channels.append(ch)


def notify(text: str) -> None:
    for ch in _channels:
        try:
            ch.send_owner(text)
        except Exception:  # noqa: BLE001
            log.exception("channel notify failed")


def start_all() -> None:
    from .. import settings
    from . import discord, telegram
    if settings.get("telegram_token") and settings.get("telegram_user_id"):
        register(telegram.Telegram())
    if settings.get("discord_token") and settings.get("discord_user_id"):
        register(discord.Discord())
    from . import ntfy
    if settings.get("ntfy_inbound") and ntfy.topic_url():   # phone → AgentOS / Claude (channels/ntfy.py)
        register(ntfy.Ntfy())
    for ch in _channels:
        ch.start()
