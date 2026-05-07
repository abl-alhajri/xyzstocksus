"""Dividend purification calculator.

When a stock is MIXED (passes business screen but earns up to 5% impermissible
income), AAOIFI requires the holder to purify dividends by donating the
proportional impermissible share to charity.

This module computes the suggested purification amount per share and per
position, surfaced in /sharia <SYMBOL> and the weekly compliance report.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PurificationEstimate:
    impermissible_share: float          # 0.0 - 1.0
    per_share_amount: float | None
    per_position_amount: float | None
    notes: str


def estimate(
    *,
    impermissible_ratio: float | None,
    dividend_per_share: float | None,
    quantity: float | None,
) -> PurificationEstimate:
    """Compute the purification amount.

    The classical AAOIFI formula:
        purify = (impermissible_revenue / total_revenue) * dividend_received
    """
    if impermissible_ratio is None or impermissible_ratio <= 0:
        return PurificationEstimate(
            impermissible_share=0.0,
            per_share_amount=None,
            per_position_amount=None,
            notes="No impermissible income reported — no purification required",
        )

    per_share = None
    per_position = None
    if dividend_per_share is not None:
        per_share = round(impermissible_ratio * dividend_per_share, 4)
        if quantity is not None:
            per_position = round(per_share * quantity, 2)

    return PurificationEstimate(
        impermissible_share=impermissible_ratio,
        per_share_amount=per_share,
        per_position_amount=per_position,
        notes=(
            f"Donate {impermissible_ratio*100:.2f}% of dividends to charity "
            "to purify the holding (AAOIFI Standard 21 §3/4/3)."
        ),
    )
