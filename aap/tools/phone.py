"""phone_update — an agent sends the owner a progress update (optionally with a screenshot) to their phone.
For long or visual tasks: "opened YouTube, now playing X" + what the screen shows. Uses ntfy (aap/notify.py)."""
from __future__ import annotations

from ..core.sdk import tool


@tool("phone_update", "Send the owner a short progress update on their phone, optionally with a screenshot of the "
      "screen (screenshot=true). Use it for long tasks or when a picture shows the result best — not for every step.",
      {"text": {"type": "string"}, "screenshot": {"type": "boolean", "default": False}})
def phone_update(ctx, text: str, screenshot: bool = False):
    from .. import notify
    title = f"AgentOS · {ctx.spec.name}"
    if screenshot:
        png = notify.screen_png()
        if png and notify.send_image(title, text, png, filename="update.jpg"):
            return {"ok": True, "sent": "text+screenshot"}
    ok = notify.send(title, text[:1500], event="finish")
    return {"ok": ok, "sent": "text" if ok else "nothing (phone notifications are off in Settings)"}
