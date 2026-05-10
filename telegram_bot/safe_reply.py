"""Shared helper for handler reply_text sends.

`safe_html_reply` mirrors the bot.send_text fallback: HTML first, then plain
text on Telegram BadRequest, so a stray `<` or unclosed tag never silently
drops a user-facing reply. Every handler that emits HTML should route
through here so the fallback is one line of code per call site.
"""
from __future__ import annotations

from core.logger import get_logger

log = get_logger("telegram.safe_reply")


async def safe_html_reply(update, body: str) -> None:
    """Send `body` as parse_mode='HTML'; on BadRequest log and retry plain."""
    try:
        from telegram.error import BadRequest  # type: ignore
    except Exception:
        BadRequest = Exception  # pragma: no cover
    try:
        await update.message.reply_text(body, parse_mode="HTML")
    except BadRequest as exc:
        log.error(
            "telegram BadRequest on handler — retrying plain text",
            extra={"telegram_err": str(exc), "body_preview": body[:500]},
        )
        await update.message.reply_text(body, parse_mode=None)
