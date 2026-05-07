"""30-day rolling correlation of a stock's returns vs BTC."""
from __future__ import annotations


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((xs[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((ys[i] - mean_y) ** 2 for i in range(n))
    if var_x <= 0 or var_y <= 0:
        return None
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return None
    return cov / denom


def daily_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            continue
        out.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return out


def btc_correlation_30d(stock_closes: list[float], btc_closes: list[float]) -> float | None:
    """Pearson correlation of last 30 daily returns. None if insufficient data."""
    if len(stock_closes) < 31 or len(btc_closes) < 31:
        return None
    sr = daily_returns(stock_closes[-31:])
    br = daily_returns(btc_closes[-31:])
    n = min(len(sr), len(br))
    if n < 5:
        return None
    return pearson(sr[-n:], br[-n:])
