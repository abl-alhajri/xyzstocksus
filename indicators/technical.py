"""Technical indicators (pure-Python, zero binary dependencies).

RSI(14), MACD(12,26,9), EMA(20/50), ATR(14), and 20d volume ratio — all
implemented in plain Python so the module imports cleanly with only pandas
on the path. We started with pandas-ta but its 0.3.x line was yanked from
PyPI; pure-Python is more robust and avoids ABI issues on Railway.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TechSummary:
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    ema_20: float | None
    ema_50: float | None
    atr_14: float | None
    last_close: float | None
    volume_ratio_20d: float | None  # current vol / 20d avg


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = (c - ema) * multiplier + ema
    return ema


def _macd(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    if len(closes) < 35:
        return None, None, None
    multiplier_12 = 2 / 13
    multiplier_26 = 2 / 27
    multiplier_sig = 2 / 10
    ema12 = sum(closes[:12]) / 12
    ema26 = sum(closes[:26]) / 26
    macd_series: list[float] = []
    for i, c in enumerate(closes):
        if i >= 12:
            ema12 = (c - ema12) * multiplier_12 + ema12
        if i >= 26:
            ema26 = (c - ema26) * multiplier_26 + ema26
            macd_series.append(ema12 - ema26)
    if len(macd_series) < 9:
        return None, None, None
    sig = sum(macd_series[:9]) / 9
    for v in macd_series[9:]:
        sig = (v - sig) * multiplier_sig + sig
    macd_val = macd_series[-1]
    return macd_val, sig, macd_val - sig


def _atr(highs: list[float], lows: list[float], closes: list[float],
         period: int = 14) -> float | None:
    if len(closes) <= period or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for v in trs[period:]:
        atr = (atr * (period - 1) + v) / period
    return atr


def summarize(df) -> TechSummary:
    """Compute the bundle of indicators heuristic_score consumes.

    `df` is a pandas DataFrame with at least Close/High/Low/Volume columns.
    """
    if df is None or len(df) == 0:
        return TechSummary(None, None, None, None, None, None, None, None, None)

    try:
        closes = [float(x) for x in df["Close"].tolist()]
        highs = [float(x) for x in df["High"].tolist()]
        lows = [float(x) for x in df["Low"].tolist()]
        volumes = [float(x) for x in df["Volume"].tolist()]
    except Exception:
        return TechSummary(None, None, None, None, None, None, None, None, None)

    macd, macd_sig, macd_hist = _macd(closes)
    last_close = closes[-1] if closes else None
    vol_ratio = None
    if len(volumes) >= 20 and volumes[-20:]:
        avg_vol = sum(volumes[-20:]) / 20
        if avg_vol > 0:
            vol_ratio = volumes[-1] / avg_vol

    return TechSummary(
        rsi_14=_rsi(closes, 14),
        macd=macd,
        macd_signal=macd_sig,
        macd_hist=macd_hist,
        ema_20=_ema(closes, 20),
        ema_50=_ema(closes, 50),
        atr_14=_atr(highs, lows, closes, 14),
        last_close=last_close,
        volume_ratio_20d=vol_ratio,
    )
