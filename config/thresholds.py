"""Thresholds — AAOIFI compliance, Sharia drift radar, and operational limits.

Pure data + tier classification helpers. No I/O, no LLM. Imported by the Sharia
engine, the orchestrator, the budget guard, and the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ShariaStatus(str, Enum):
    """Three-tier classification per AAOIFI Standard 21."""

    HALAL = "HALAL"   # شرعي
    MIXED = "MIXED"   # مختلط
    HARAM = "HARAM"   # غير شرعي


class RiskTier(str, Enum):
    """Per-ratio alert tier — used for Telegram badges and dashboard colour."""

    GREEN = "GREEN"     # ratio < 25%        (safe)
    YELLOW = "YELLOW"   # 25% <= ratio < 30% (warning)
    ORANGE = "ORANGE"   # 30% <= ratio < 33% (minor breach)
    RED = "RED"         # ratio >= 33%       (clear breach)


# AAOIFI Standard 21 financial ratio thresholds
DEBT_MAX_RATIO = 0.30          # interest-bearing debt / market cap
CASH_MAX_RATIO = 0.30          # cash + interest-bearing securities / market cap
IMPERMISSIBLE_MAX_RATIO = 0.05  # impermissible income / total revenue

# Tier boundaries (apply to debt and cash ratios; impermissible uses its own scale)
TIER_YELLOW = 0.25
TIER_ORANGE = 0.30
TIER_RED = 0.33

# Sharia Drift Radar — predictive early warning
DRIFT_PP_PER_QUARTER = 0.02   # 2 percentage points per quarter
DRIFT_PROXIMITY_PP = 0.03     # within 3pp of breach

# Confidence band for Round 2 cross-critique (refined: tighter band)
R2_BAND = (0.60, 0.70)

# BTC dump protection
BTC_DUMP_PCT = 0.05           # 5% drop
BTC_DUMP_WINDOW_MIN = 60      # over 60 minutes
BTC_DUMP_COOLDOWN_MIN = 60    # skip btc_full agents for next 60 minutes

# Telegram alert dedup
DEDUP_WINDOW_HOURS = 4
DEDUP_CONFIDENCE_JUMP = 0.10  # 10pp jump bypasses dedup

# Min confidence to emit Telegram signal
MIN_CONFIDENCE_FOR_ALERT = 0.65

# Earnings blackout window (skip generating fresh signals)
EARNINGS_BLACKOUT_HOURS = 48

# Insider cluster detector
INSIDER_CLUSTER_MIN = 3        # at least 3 insiders
INSIDER_CLUSTER_DAYS = 14      # within 14 days
INSIDER_CLUSTER_REQUIRE_OFFICER = True  # at least one CFO/CEO/President


@dataclass(frozen=True)
class TierBreakdown:
    debt_tier: RiskTier
    cash_tier: RiskTier
    impermissible_tier: RiskTier
    overall: ShariaStatus
    notes: str = ""


def classify_ratio(ratio: float | None) -> RiskTier:
    """Classify a debt or cash ratio into the four-tier alert system."""
    if ratio is None:
        return RiskTier.YELLOW  # unknown → conservative warning
    if ratio < TIER_YELLOW:
        return RiskTier.GREEN
    if ratio < TIER_ORANGE:
        return RiskTier.YELLOW
    if ratio < TIER_RED:
        return RiskTier.ORANGE
    return RiskTier.RED


def classify_impermissible(ratio: float | None) -> RiskTier:
    """Impermissible income uses a tighter scale (5% absolute cap)."""
    if ratio is None:
        return RiskTier.YELLOW
    if ratio < 0.01:
        return RiskTier.GREEN
    if ratio < 0.03:
        return RiskTier.YELLOW
    if ratio < IMPERMISSIBLE_MAX_RATIO:
        return RiskTier.ORANGE
    return RiskTier.RED


def derive_status(
    debt_ratio: float | None,
    cash_ratio: float | None,
    impermissible_ratio: float | None,
) -> TierBreakdown:
    """Combine ratio tiers into a single ShariaStatus."""
    debt_tier = classify_ratio(debt_ratio)
    cash_tier = classify_ratio(cash_ratio)
    imp_tier = classify_impermissible(impermissible_ratio)

    # Any RED tier (>=33% on debt/cash, or >=5% impermissible) → HARAM
    if RiskTier.RED in (debt_tier, cash_tier, imp_tier):
        overall = ShariaStatus.HARAM
        note = "Hard breach on at least one ratio"
    # Any ORANGE on debt/cash, or impermissible >=3% → MIXED
    elif RiskTier.ORANGE in (debt_tier, cash_tier, imp_tier):
        overall = ShariaStatus.MIXED
        note = "Borderline breach — monitor closely"
    # YELLOW on any → MIXED (conservative)
    elif RiskTier.YELLOW in (debt_tier, cash_tier, imp_tier):
        overall = ShariaStatus.MIXED
        note = "Within thresholds but elevated"
    else:
        overall = ShariaStatus.HALAL
        note = "All ratios comfortably within AAOIFI limits"

    return TierBreakdown(
        debt_tier=debt_tier,
        cash_tier=cash_tier,
        impermissible_tier=imp_tier,
        overall=overall,
        notes=note,
    )


def is_drift_warning(
    quarterly_ratios: list[float],
    current_ratio: float,
    threshold: float = DEBT_MAX_RATIO,
) -> bool:
    """Sharia Drift Radar — predictive early warning.

    Triggers when the rolling slope of the last 4 quarters is rising at
    >=DRIFT_PP_PER_QUARTER AND the current ratio is within DRIFT_PROXIMITY_PP
    of the breach threshold (default 30%). Catches stocks heading toward a
    tier change weeks before it lands.
    """
    if len(quarterly_ratios) < 4 or current_ratio is None:
        return False

    last4 = quarterly_ratios[-4:]
    avg_qoq_change = (last4[-1] - last4[0]) / 3.0  # 3 step-overs across 4 quarters
    rising_fast = avg_qoq_change >= DRIFT_PP_PER_QUARTER
    near_breach = (threshold - current_ratio) <= DRIFT_PROXIMITY_PP and current_ratio < threshold

    return rising_fast and near_breach
