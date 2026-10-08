"""Telegram channel (Bot API long polling, no extra deps). Only the allowlisted user id is served.

* Text message -> Main Agent run; progress shown as a "typing" status; final answer sent back.
* Approval requests -> message with inline Approve / Deny buttons.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from .. import bus, settings
from ..core import approvals

log = logging.getLogger("aap.telegram")


class Telegram(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="telegram")
        self.token = settings.get("telegram_token")
        self.owner = str(settings.get("telegram_user_id"))
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.conversation_id: str | None = None
        bus.on(self._on_bus)

    def _post(self, method: str, **data):
        try:
            return httpx.post(f"{self.api}/{method}", json=data, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            log.warning("telegram %s failed: %s", method, e)
            return {}

    def send_owner(self, text: str, **extra) -> None:
        for i in range(0, max(len(text), 1), 3900):
            self._post("sendMessage", chat_id=self.owner, text=text[i:i + 3900], **extra)

    def _on_bus(self, channel: str, data: dict) -> None:
        if channel == "approval" and data.get("status") == "pending":
            kb = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"ap:{data['id']}"},
                                       {"text": "⛔ Deny", "callback_data": f"dn:{data['id']}"}]]}
            from .. import souls
            self.send_owner(f"🔐 {souls.assistant_name()} needs your approval ({data['agent_id']}):\n{data['title']}",
                            reply_markup=kb)

    def run(self) -> None:
        offset = 0
        while True:
            try:
                r = httpx.get(f"{self.api}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=60).json()
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    self._handle(u)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram poll error: %s", e)
                time.sleep(5)

    def _handle(self, u: dict) -> None:
        from ..core.runtime import runtime
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq["from"]["id"]) != self.owner:
                return
            kind, aid = cq["data"].split(":", 1)
            approvals.decide(aid, "approve" if kind == "ap" else "deny")
            self._post("answerCallbackQuery", callback_query_id=cq["id"], text="Done")
            return
        msg = u.get("message") or {}
        if str((msg.get("from") or {}).get("id")) != self.owner or not msg.get("text"):
            return
        text = msg["text"]
        if text.strip() == "/new":
            from .. import souls
            self.conversation_id = None
            ident = souls.get_identity()
            self.send_owner(f"{ident['emoji']} {ident['name']}: new conversation started.")
            return
        self._post("sendChatAction", chat_id=self.owner, action="typing")

        def done(run, rep, final):
            self.send_owner(final or rep.summary)

        _, self.conversation_id = runtime.start_chat(text, self.conversation_id, kind="telegram", on_done=done)
