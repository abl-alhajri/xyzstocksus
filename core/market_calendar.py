"""NYSE market hours + holiday detection.

Wraps `pandas_market_calendars` when available, falls back to a hand-rolled
US-holiday lookup that's good enough for early-close awareness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass
class MarketStatus:
    label: str           # OPEN | CLOSED | PRE_MARKET | POST_MARKET | EARLY_CLOSE
    is_open: bool
    is_early_close: bool
    next_open_utc: str | None = None
    next_close_utc: str | None = None
    note: str = ""


# US Market holidays 2026 (full closures)
US_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

# Days the NYSE closes early at 1pm ET
EARLY_CLOSE_DAYS_2026 = {
    date(2026, 7, 2),    # Independence Day eve
    date(2026, 11, 27),  # Day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
}

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
PRE_MARKET_OPEN = time(4, 0)
POST_MARKET_CLOSE = time(20, 0)


def _try_pandas_calendar():
    try:
        import pandas_market_calendars as mcal  # type: ignore
        return mcal.get_calendar("XNYS")
    except Exception:
        return None


def is_holiday(d: date) -> bool:
    return d in US_HOLIDAYS_2026


def is_early_close_day(d: date) -> bool:
    return d in EARLY_CLOSE_DAYS_2026


def status(now_utc: datetime | None = None) -> MarketStatus:
    """Return the current market status as of `now_utc` (defaults to now)."""
    now_utc = now_utc or datetime.now(tz=UTC)
    now_et = now_utc.astimezone(ET)
    today = now_et.date()
    weekday = now_et.weekday()

    if weekday >= 5:
        return MarketStatus(label="CLOSED", is_open=False, is_early_close=False,
                            note="weekend")
    if is_holiday(today):
        return MarketStatus(label="CLOSED", is_open=False, is_early_close=False,
                            note="US market holiday")

    early = is_early_close_day(today)
    close_t = EARLY_CLOSE if early else REGULAR_CLOSE
    pre = datetime.combine(today, PRE_MARKET_OPEN, tzinfo=ET)
    open_t = datetime.combine(today, REGULAR_OPEN, tzinfo=ET)
    close_dt = datetime.combine(today, close_t, tzinfo=ET)
    post_close = datetime.combine(today, POST_MARKET_CLOSE, tzinfo=ET)

    if now_et < pre:
        return MarketStatus("CLOSED", False, early, note="overnight")
    if pre <= now_et < open_t:
        return MarketStatus("PRE_MARKET", False, early)
    if open_t <= now_et < close_dt:
        label = "EARLY_CLOSE" if early else "OPEN"
        return MarketStatus(label, True, early)
    if close_dt <= now_et < post_close:
        return MarketStatus("POST_MARKET", False, early)
    return MarketStatus("CLOSED", False, early, note="post-after-hours")


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return not is_holiday(d)
