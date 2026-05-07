"""0-100 heuristic score per stock per scan. No LLM, $0 cost.

Goal: cheap pre-filter that surfaces the top ~15 candidates for the Haiku
pre-screen. Score is a weighted blend of:
  - momentum    (RSI band + MACD histogram sign)
  - trend       (EMA20 vs EMA50)
  - volume      (current volume vs 20d average)
  - btc_align   (rolling 30d correlation × BTC regime alignment)

The score is intentionally explainable — every contribution is clamped to a
named max so it's easy to reason about why a symbol scored what it did.
"""
from __future__ import annotations

from dataclasses import dataclass

from indicators.technical import TechSummary


@dataclass
class ScoreBreakdown:
    momentum: float          # 0-30
    trend: float             # 0-25
    volume: float            # 0-20
    btc_align: float         # 0-25
    total: float             # sum, capped 0-100
    notes: list[str]


def score(
    *,
    tech: TechSummary,
    btc_corr_30d: float | None,
    btc_regime: str | None,           # BULL | BEAR | NEUTRAL | None
    btc_beta: float = 0.0,
    is_btc_full: bool = False,
) -> ScoreBreakdown:
    notes: list[str] = []

    # --- Momentum (0-30) ------------------------------------------------
    momentum = 0.0
    if tech.rsi_14 is not None:
        # Reward 50-65 (sweet spot), penalise extremes
        if 50 <= tech.rsi_14 <= 65:
            momentum += 18
            notes.append(f"RSI {tech.rsi_14:.1f} in 50-65 sweet spot")
        elif 35 <= tech.rsi_14 < 50:
            momentum += 10
            notes.append(f"RSI {tech.rsi_14:.1f} below 50 — basing")
        elif 65 < tech.rsi_14 <= 75:
            momentum += 8
            notes.append(f"RSI {tech.rsi_14:.1f} extended")
        elif tech.rsi_14 < 30:
            momentum += 14
            notes.append(f"RSI {tech.rsi_14:.1f} oversold rebound candidate")
        else:
            notes.append(f"RSI {tech.rsi_14:.1f} unfavourable")
    if tech.macd_hist is not None:
        if tech.macd_hist > 0:
            momentum += 12
            notes.append("MACD histogram positive")
        elif tech.macd_hist < 0:
            momentum += 0
            notes.append("MACD histogram negative")
    momentum = min(momentum, 30)

    # --- Trend (0-25) ---------------------------------------------------
    trend = 0.0
    if tech.ema_20 is not None and tech.ema_50 is not None and tech.last_close is not None:
        if tech.last_close > tech.ema_20 > tech.ema_50:
            trend = 25
            notes.append("Price > EMA20 > EMA50 (uptrend)")
        elif tech.last_close > tech.ema_20:
            trend = 15
            notes.append("Price > EMA20")
        elif tech.last_close < tech.ema_20 < tech.ema_50:
            trend = 0
            notes.append("Price < EMA20 < EMA50 (downtrend)")
        else:
            trend = 8
            notes.append("Mixed trend")

    # --- Volume (0-20) --------------------------------------------------
    volume = 0.0
    if tech.volume_ratio_20d is not None:
        if tech.volume_ratio_20d >= 2.0:
            volume = 20
            notes.append(f"Volume {tech.volume_ratio_20d:.1f}x avg — strong")
        elif tech.volume_ratio_20d >= 1.3:
            volume = 14
            notes.append(f"Volume {tech.volume_ratio_20d:.1f}x avg — elevated")
        elif tech.volume_ratio_20d >= 0.8:
            volume = 8
        else:
            volume = 4
            notes.append(f"Volume {tech.volume_ratio_20d:.1f}x avg — light")

    # --- BTC alignment (0-25) -------------------------------------------
    btc_align = 0.0
    if is_btc_full:
        # For btc_full names, BTC alignment matters a lot
        if btc_corr_30d is not None and btc_regime:
            # Reward correlation that matches the regime
            if btc_regime == "BULL" and btc_corr_30d > 0.5:
                btc_align = 25
                notes.append(f"BTC bull + corr {btc_corr_30d:.2f} aligned")
            elif btc_regime == "BEAR" and btc_corr_30d > 0.5:
                btc_align = 5
                notes.append(f"BTC bear + corr {btc_corr_30d:.2f} = headwind")
            elif btc_regime == "BULL" and btc_corr_30d < 0:
                btc_align = 8
                notes.append("BTC bull but stock decoupled")
            elif btc_regime == "NEUTRAL":
                btc_align = 12
            else:
                btc_align = 8
        # btc_beta amplification
        btc_align = min(btc_align * (1 + min(btc_beta, 3) * 0.05), 25)
    else:
        # For non-BTC names, btc_align contributes lightly to overall
        if btc_corr_30d is not None and btc_regime == "BULL" and btc_corr_30d > 0.3:
            btc_align = 8
            notes.append("Modest BTC tailwind")
        else:
            btc_align = 5

    total = max(0.0, min(momentum + trend + volume + btc_align, 100.0))
    return ScoreBreakdown(
        momentum=round(momentum, 2),
        trend=round(trend, 2),
        volume=round(volume, 2),
        btc_align=round(btc_align, 2),
        total=round(total, 2),
        notes=notes,
    )
