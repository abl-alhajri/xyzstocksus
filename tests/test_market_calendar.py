"""Commit 12 tests — market calendar (NYSE hours, holidays, early close)."""
from __future__ import annotations

from datetime import datetime, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def test_weekend_closed():
    from core.market_calendar import status
    sat = datetime(2026, 5, 9, 14, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=sat)
    assert s.is_open is False
    assert s.label == "CLOSED"
    assert s.note == "weekend"


def test_pre_market():
    from core.market_calendar import status
    dt = datetime(2026, 5, 7, 7, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=dt)
    assert s.label == "PRE_MARKET"


def test_open():
    from core.market_calendar import status
    dt = datetime(2026, 5, 7, 11, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=dt)
    assert s.label == "OPEN"
    assert s.is_open is True


def test_post_market():
    from core.market_calendar import status
    dt = datetime(2026, 5, 7, 17, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=dt)
    assert s.label == "POST_MARKET"


def test_closed_overnight():
    from core.market_calendar import status
    dt = datetime(2026, 5, 7, 22, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=dt)
    assert s.label == "CLOSED"
    assert s.note == "post-after-hours"


def test_holiday_closed():
    from core.market_calendar import status, is_holiday
    new_years = datetime(2026, 1, 1, 11, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=new_years)
    assert s.is_open is False
    assert is_holiday(date(2026, 1, 1))


def test_early_close_day_marked():
    from core.market_calendar import status, is_early_close_day
    # Christmas Eve at 12pm ET → still open (early close at 1pm)
    pre = datetime(2026, 12, 24, 12, 0, tzinfo=ET).astimezone(UTC)
    s = status(now_utc=pre)
    assert s.is_open is True
    assert s.is_early_close is True
    assert s.label == "EARLY_CLOSE"
    # Christmas Eve at 1:30pm ET → POST_MARKET
    post = datetime(2026, 12, 24, 13, 30, tzinfo=ET).astimezone(UTC)
    s2 = status(now_utc=post)
    assert s2.is_open is False
    assert s2.label == "POST_MARKET"
    assert is_early_close_day(date(2026, 12, 24))


def test_is_trading_day():
    from core.market_calendar import is_trading_day
    assert is_trading_day(date(2026, 5, 7))    # Thursday
    assert not is_trading_day(date(2026, 5, 9))  # Saturday
    assert not is_trading_day(date(2026, 1, 1))  # NYE holiday
