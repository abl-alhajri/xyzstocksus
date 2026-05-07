"""python-telegram-bot v20+ application factory.

The web process imports `build_application()` and runs it as a polling task
alongside Flask. The same module exposes `send_text` for one-shot pushes
from the scheduler / orchestrator.
"""
from __future__ import annotations

import asyncio

from config.settings import settings
from core.logger import get_logger

log = get_logger("telegram.bot")


async def send_text(text: str, *, parse_mode: str | None = "Markdown",
                    chat_id: str | None = None) -> int | None:
    """Fire-and-forget message. Returns message_id or None on failure."""
    if not settings.telegram_bot_token:
        log.info("telegram skipped — no token configured")
        return None
    target = chat_id or settings.telegram_chat_id
    if not target:
        log.info("telegram skipped — no chat_id configured")
        return None
    try:
        from telegram import Bot  # type: ignore
        bot = Bot(token=settings.telegram_bot_token)
        msg = await bot.send_message(chat_id=int(target), text=text,
                                     parse_mode=parse_mode,
                                     disable_web_page_preview=True)
        return int(msg.message_id) if msg else None
    except Exception as exc:
        log.warning("telegram send failed", extra={"err": str(exc)})
        return None


def send_text_sync(text: str, *, parse_mode: str | None = "Markdown",
                   chat_id: str | None = None) -> int | None:
    try:
        return asyncio.run(send_text(text, parse_mode=parse_mode, chat_id=chat_id))
    except RuntimeError:
        # Already in a loop — schedule
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                send_text(text, parse_mode=parse_mode, chat_id=chat_id))
        finally:
            loop.close()


def build_application():
    """Build the PTB Application with handlers registered.

    Imported lazily by workers/web.py because the SDK isn't strictly required
    for the Flask /health endpoint.
    """
    from telegram.ext import Application, CommandHandler  # type: ignore

    if not settings.telegram_bot_token:
        log.info("Telegram disabled — no bot token in env")
        return None

    app = Application.builder().token(settings.telegram_bot_token).build()

    from telegram_bot.handlers import basic, watchlist
    app.add_handler(CommandHandler("start", basic.start))
    app.add_handler(CommandHandler("help", basic.help_cmd))
    app.add_handler(CommandHandler("status", basic.status))
    app.add_handler(CommandHandler("watch", watchlist.watch))

    log.info("Telegram application ready")
    return app
