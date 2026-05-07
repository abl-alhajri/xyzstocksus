"""Telegram alert dedup wrapper.

Wraps db.repos.signals.should_dedup with the configured window + jump
threshold so callers don't import config in two places.
"""
from __future__ import annotations

from config.settings import settings
from db.repos import signals as signals_repo


def should_suppress(symbol: str, *, new_confidence: float) -> bool:
    return signals_repo.should_dedup(
        symbol,
        new_confidence=new_confidence,
        window_hours=settings.dedup_window_hours,
        confidence_jump=settings.dedup_confidence_jump,
    )
