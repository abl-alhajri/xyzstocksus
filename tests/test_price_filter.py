"""Tests for core.price_filter — [MIN, MAX] price-band gating at scan time."""
from __future__ import annotations

from core.price_filter import is_in_range, reason_out_of_range


def test_in_range_within_band():
    assert is_in_range(100.0, min_usd=1.0, max_usd=300.0)
    assert is_in_range(1.0, min_usd=1.0, max_usd=300.0)        # at min bound
    assert is_in_range(300.0, min_usd=1.0, max_usd=300.0)      # at max bound


def test_in_range_above_max_excluded():
    assert not is_in_range(310.0, min_usd=1.0, max_usd=300.0)
    assert not is_in_range(999.99, min_usd=1.0, max_usd=300.0)


def test_in_range_below_min_excluded():
    assert not is_in_range(0.50, min_usd=1.0, max_usd=300.0)
    assert not is_in_range(0.0, min_usd=1.0, max_usd=300.0)


def test_in_range_none_passes_through():
    # Missing-data must not cause silent drops — scan loop handles missing frames.
    assert is_in_range(None, min_usd=1.0, max_usd=300.0)


def test_uses_settings_defaults_when_no_override():
    # Settings defaults are $1 / $300 per spec.
    assert is_in_range(150.0)
    assert not is_in_range(0.0)
    assert not is_in_range(1000.0)


def test_reason_out_of_range_message_includes_bounds():
    msg = reason_out_of_range(500.0, min_usd=1.0, max_usd=300.0)
    assert "$500.00" in msg
    assert "$1.00" in msg
    assert "$300.00" in msg
