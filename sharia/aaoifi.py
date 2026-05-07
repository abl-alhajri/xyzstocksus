"""AAOIFI Standard 21 — wrappers around config.thresholds for the sharia engine.

Re-exports the canonical constants and adds a few convenience helpers so the
business screen + ratio engine + Sharia officer agent share one source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.thresholds import (
    DEBT_MAX_RATIO,
    CASH_MAX_RATIO,
    IMPERMISSIBLE_MAX_RATIO,
    TIER_YELLOW,
    TIER_ORANGE,
    TIER_RED,
    DRIFT_PP_PER_QUARTER,
    DRIFT_PROXIMITY_PP,
    RiskTier,
    ShariaStatus,
    classify_ratio,
    classify_impermissible,
    derive_status,
    is_drift_warning,
    TierBreakdown,
)


@dataclass(frozen=True)
class AAOIFIThresholds:
    debt_max: float = DEBT_MAX_RATIO
    cash_max: float = CASH_MAX_RATIO
    impermissible_max: float = IMPERMISSIBLE_MAX_RATIO
    tier_yellow: float = TIER_YELLOW
    tier_orange: float = TIER_ORANGE
    tier_red: float = TIER_RED


THRESHOLDS = AAOIFIThresholds()


def severity_for_status(status: ShariaStatus) -> str:
    """Map a ShariaStatus to an alert severity for compliance_alerts rows."""
    if status == ShariaStatus.HARAM:
        return "CRITICAL"
    if status == ShariaStatus.MIXED:
        return "WARN"
    return "INFO"


def worst_tier(*tiers: RiskTier) -> RiskTier:
    """Return the worst (most severe) tier from any number of inputs."""
    order = {RiskTier.GREEN: 0, RiskTier.YELLOW: 1, RiskTier.ORANGE: 2, RiskTier.RED: 3}
    return max(tiers, key=lambda t: order[t])


__all__ = [
    "DEBT_MAX_RATIO",
    "CASH_MAX_RATIO",
    "IMPERMISSIBLE_MAX_RATIO",
    "TIER_YELLOW",
    "TIER_ORANGE",
    "TIER_RED",
    "DRIFT_PP_PER_QUARTER",
    "DRIFT_PROXIMITY_PP",
    "RiskTier",
    "ShariaStatus",
    "classify_ratio",
    "classify_impermissible",
    "derive_status",
    "is_drift_warning",
    "TierBreakdown",
    "AAOIFIThresholds",
    "THRESHOLDS",
    "severity_for_status",
    "worst_tier",
]
