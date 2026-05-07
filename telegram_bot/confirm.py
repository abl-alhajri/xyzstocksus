"""Inline-button confirmation flow with 60s TTL.

Used by destructive commands (/disable, /enable, /threshold, /pause, /resume,
/sell). The handler stores a pending action keyed by callback_data; when the
user taps Confirm, we execute it. Cancel or 60s timeout discards.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.logger import get_logger

log = get_logger("telegram.confirm")

TTL_SECONDS = 60


@dataclass
class PendingAction:
    action_id: str
    description: str
    created_at: float
    callback: Callable[[], Awaitable[str]]


_PENDING: dict[str, PendingAction] = {}


def register(description: str, callback: Callable[[], Awaitable[str]]) -> tuple[str, str]:
    """Register a pending destructive action.

    Returns (action_id, confirm_button_text).
    """
    _gc()
    action_id = uuid.uuid4().hex[:8]
    _PENDING[action_id] = PendingAction(
        action_id=action_id,
        description=description,
        created_at=time.time(),
        callback=callback,
    )
    return action_id, "✅ Confirm"


def build_keyboard(action_id: str):
    """Return an InlineKeyboardMarkup with Confirm + Cancel buttons.

    Returns None when telegram SDK isn't installed (caller falls back to
    plain-text instructions).
    """
    try:
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton  # type: ignore
    except ImportError:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{action_id}"),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"cancel:{action_id}"),
    ]])


async def handle_callback(update, context) -> str:
    """Telegram callback_query handler. Returns a string for the reply text."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if ":" not in data:
        return "Unknown action."
    verb, action_id = data.split(":", 1)
    action = _PENDING.pop(action_id, None)

    if action is None:
        return "This action expired or was already handled."

    if time.time() - action.created_at > TTL_SECONDS:
        return "Action timed out (60s window). Re-issue the command if you still want it."

    if verb == "cancel":
        return f"Cancelled: {action.description}"

    if verb == "confirm":
        try:
            return await action.callback()
        except Exception as exc:
            log.warning("confirm action failed",
                        extra={"action": action.description, "err": str(exc)})
            return f"Action failed: {exc}"

    return "Unknown action verb."


def _gc() -> None:
    """Drop expired pending actions to keep memory bounded."""
    now = time.time()
    expired = [k for k, v in _PENDING.items() if now - v.created_at > TTL_SECONDS]
    for k in expired:
        _PENDING.pop(k, None)
