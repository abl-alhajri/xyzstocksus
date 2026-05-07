"""Earnings calendar — used for the 48h blackout (skip generating new signals)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config.thresholds import EARNINGS_BLACKOUT_HOURS
from core import cache
from core.logger import get_logger

log = get_logger("data.earnings")


@dataclass
class EarningsEntry:
    symbol: str
    date: str            # ISO datetime if available, else YYYY-MM-DD
    when: str | None     # 'BMO' | 'AMC' | None


def next_earnings(symbol: str, *, use_cache: bool = True) -> EarningsEntry | None:
    """Best-effort lookup via yfinance.Ticker.calendar / .earnings_dates.

    Returns None if no upcoming date is available; the orchestrator treats
    None as "no blackout".
    """
    sym = symbol.upper()
    if use_cache:
        cached = cache.get("earnings", sym, cache.TTL_EARNINGS)
        if cached is not None:
            return cached

    try:
        import yfinance as yf  # type: ignore
    except ImportError:  # pragma: no cover
        return None

    try:
        t = yf.Ticker(sym)
        # earnings_dates returns a DataFrame indexed by date
        df = getattr(t, "earnings_dates", None)
        try:
            df = df  # property access may raise
        except Exception:
            df = None
        if df is None or len(df) == 0:
            cal = getattr(t, "calendar", None)
            if cal is not None and "Earnings Date" in cal:
                next_date = cal["Earnings Date"]
                date_str = str(next_date[0]) if hasattr(next_date, "__len__") else str(next_date)
                entry = EarningsEntry(symbol=sym, date=date_str, when=None)
                cache.set_("earnings", sym, entry)
                return entry
            return None

        upcoming = df[df.index >= datetime.now(timezone.utc)]
        if len(upcoming) == 0:
            return None
        first_idx = upcoming.index[0]
        entry = EarningsEntry(symbol=sym, date=str(first_idx.isoformat()), when=None)
        if use_cache:
            cache.set_("earnings", sym, entry)
        return entry
    except Exception as exc:
        log.warning("earnings lookup failed", extra={"symbol": sym, "err": str(exc)})
        return None


def in_blackout(symbol: str, hours: int = EARNINGS_BLACKOUT_HOURS) -> bool:
    """True if next earnings date is within `hours` from now."""
    entry = next_earnings(symbol)
    if not entry or not entry.date:
        return False
    try:
        dt = datetime.fromisoformat(entry.date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    delta = dt - datetime.now(timezone.utc)
    return timedelta(0) <= delta <= timedelta(hours=hours)
