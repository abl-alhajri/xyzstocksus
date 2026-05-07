"""Commit 5 tests — technical indicators, correlation, heuristic scoring."""
from __future__ import annotations


# ----------------------------- RSI ---------------------------------------

def test_rsi_returns_none_with_short_series():
    from indicators.technical import _rsi
    assert _rsi([1.0, 2.0, 3.0]) is None


def test_rsi_pure_uptrend_high():
    from indicators.technical import _rsi
    closes = [float(i) for i in range(1, 30)]
    val = _rsi(closes, 14)
    assert val is not None
    # Pure uptrend → RSI close to 100 (no losses → division by zero handled)
    assert val == 100.0


def test_rsi_pure_downtrend_low():
    from indicators.technical import _rsi
    closes = [float(30 - i) for i in range(30)]
    val = _rsi(closes, 14)
    assert val is not None
    assert val < 5.0


# ----------------------------- EMA / MACD --------------------------------

def test_ema_basic():
    from indicators.technical import _ema
    closes = [10.0] * 50
    assert _ema(closes, 20) == 10.0


def test_macd_returns_floats_with_enough_data():
    from indicators.technical import _macd
    closes = [100 + i * 0.5 + (i % 5) * 0.3 for i in range(80)]
    macd, sig, hist = _macd(closes)
    assert macd is not None and sig is not None and hist is not None


# ----------------------------- ATR ---------------------------------------

def test_atr_simple_range():
    from indicators.technical import _atr
    highs = [11.0] * 30
    lows = [9.0] * 30
    closes = [10.0] * 30
    val = _atr(highs, lows, closes, 14)
    assert val is not None
    assert 1.5 <= val <= 2.5


# ----------------------------- correlation -------------------------------

def test_pearson_perfect_positive():
    from indicators.correlation import pearson
    xs = [1, 2, 3, 4, 5]
    ys = [2, 4, 6, 8, 10]
    val = pearson(xs, ys)
    assert val is not None
    assert abs(val - 1.0) < 1e-9


def test_pearson_perfect_negative():
    from indicators.correlation import pearson
    xs = [1, 2, 3, 4, 5]
    ys = [10, 8, 6, 4, 2]
    val = pearson(xs, ys)
    assert val is not None
    assert abs(val - (-1.0)) < 1e-9


def test_pearson_zero_variance():
    from indicators.correlation import pearson
    xs = [5, 5, 5, 5]
    ys = [1, 2, 3, 4]
    assert pearson(xs, ys) is None


def test_btc_correlation_30d_uses_returns():
    from indicators.correlation import btc_correlation_30d
    # 31-point closes that trend identically
    s = [100 + i for i in range(35)]
    b = [100 + i for i in range(35)]
    val = btc_correlation_30d(s, b)
    # Both pure linear trends → returns are correlated but not perfectly (each
    # daily return is decreasing as price rises). Just verify it's positive.
    assert val is not None
    assert val > 0.5


# ----------------------------- heuristic score ---------------------------

def test_score_high_when_uptrend_volume_btc_align():
    from indicators.heuristic_score import score
    from indicators.technical import TechSummary

    tech = TechSummary(
        rsi_14=58.0,
        macd=1.2, macd_signal=0.8, macd_hist=0.4,
        ema_20=110.0, ema_50=100.0,
        atr_14=2.0,
        last_close=115.0,
        volume_ratio_20d=2.5,
    )
    breakdown = score(
        tech=tech,
        btc_corr_30d=0.7,
        btc_regime="BULL",
        btc_beta=2.0,
        is_btc_full=True,
    )
    assert breakdown.total >= 80
    assert breakdown.momentum >= 25
    assert breakdown.trend == 25
    assert breakdown.volume == 20


def test_score_low_when_downtrend_and_btc_bear():
    from indicators.heuristic_score import score
    from indicators.technical import TechSummary

    tech = TechSummary(
        rsi_14=35.0,
        macd=-1.2, macd_signal=-0.8, macd_hist=-0.4,
        ema_20=95.0, ema_50=100.0,
        atr_14=2.0,
        last_close=92.0,
        volume_ratio_20d=0.6,
    )
    breakdown = score(
        tech=tech,
        btc_corr_30d=0.6,
        btc_regime="BEAR",
        btc_beta=2.5,
        is_btc_full=True,
    )
    assert breakdown.total <= 35
    assert breakdown.trend == 0


def test_score_handles_missing_data():
    from indicators.heuristic_score import score
    from indicators.technical import TechSummary

    tech = TechSummary(None, None, None, None, None, None, None, None, None)
    breakdown = score(
        tech=tech, btc_corr_30d=None, btc_regime=None, btc_beta=0.0, is_btc_full=False,
    )
    assert 0 <= breakdown.total <= 100
    # No usable signals → mostly 0 except the small "non-btc default" alignment
    assert breakdown.total <= 10


def test_score_btc_full_amplifies_alignment():
    from indicators.heuristic_score import score
    from indicators.technical import TechSummary

    tech = TechSummary(
        rsi_14=55.0, macd=0.5, macd_signal=0.3, macd_hist=0.2,
        ema_20=100.0, ema_50=98.0, atr_14=1.5, last_close=101.0, volume_ratio_20d=1.4,
    )
    btc_full = score(
        tech=tech, btc_corr_30d=0.7, btc_regime="BULL", btc_beta=2.5, is_btc_full=True,
    )
    standard = score(
        tech=tech, btc_corr_30d=0.7, btc_regime="BULL", btc_beta=0.3, is_btc_full=False,
    )
    assert btc_full.btc_align > standard.btc_align


def test_score_total_capped_at_100():
    from indicators.heuristic_score import score
    from indicators.technical import TechSummary

    tech = TechSummary(
        rsi_14=58.0, macd=2.0, macd_signal=1.0, macd_hist=1.0,
        ema_20=120.0, ema_50=100.0, atr_14=3.0, last_close=130.0, volume_ratio_20d=5.0,
    )
    breakdown = score(
        tech=tech, btc_corr_30d=0.9, btc_regime="BULL", btc_beta=3.0, is_btc_full=True,
    )
    assert breakdown.total <= 100.0
