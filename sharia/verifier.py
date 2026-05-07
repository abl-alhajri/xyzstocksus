"""Sharia verifier — orchestrates business screen + ratio compute + tier derivation.

This is the canonical entry point used by:
  - the migration's first-time scan,
  - the daily 10-Q monitor,
  - the weekly full scan,
  - the Sharia officer agent (which only displays results),
  - the /sharia <SYMBOL> command.

It NEVER calls an LLM. All ratios come from yfinance + SEC EDGAR; the LLM
later only renders explanations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger
from db.repos import sharia as sharia_repo, stocks as stocks_repo
from sharia.aaoifi import (
    AAOIFIThresholds,
    THRESHOLDS,
    RiskTier,
    ShariaStatus,
    classify_impermissible,
    classify_ratio,
    derive_status,
    severity_for_status,
    worst_tier,
    is_drift_warning,
)
from sharia.business_screen import BusinessVerdict, screen as business_screen
from sharia.ratios import RatioInputs, RatiosComputed, compute, from_company_facts, from_yfinance_info

log = get_logger("sharia.verifier")


@dataclass
class VerificationResult:
    symbol: str
    status: ShariaStatus
    business: BusinessVerdict
    ratios: RatiosComputed | None
    debt_tier: RiskTier | None
    cash_tier: RiskTier | None
    impermissible_tier: RiskTier | None
    overall_tier: RiskTier | None
    drift_warning: bool
    notes: str
    fetched_at: str


def verify(
    symbol: str,
    *,
    market_cap: float | None = None,
    yfinance_info: dict[str, Any] | None = None,
    company_facts: dict[str, Any] | None = None,
    sec_sic: str | int | None = None,
    yfinance_industry: str | None = None,
    persist: bool = True,
) -> VerificationResult:
    """End-to-end Sharia verification for a single symbol.

    Pulls business screen first (cheap), then computes ratios (only if business
    screen passes), then derives status and persists to the DB.

    All inputs are optional — if both yfinance_info and company_facts are
    None, the result is HARAM-or-PENDING based on the business screen alone.
    """
    sym = symbol.upper()
    fetched_at = datetime.now(timezone.utc).isoformat()

    biz = business_screen(symbol=sym, sec_sic=sec_sic,
                          yfinance_industry=yfinance_industry)

    # If business screen fails → HARAM, no need to compute ratios
    if not biz.passed:
        result = VerificationResult(
            symbol=sym,
            status=ShariaStatus.HARAM,
            business=biz,
            ratios=None,
            debt_tier=None,
            cash_tier=None,
            impermissible_tier=None,
            overall_tier=RiskTier.RED,
            drift_warning=False,
            notes=f"Business screen failed: {biz.reason}",
            fetched_at=fetched_at,
        )
        if persist:
            _persist(result)
        return result

    # Prefer SEC company_facts for accuracy; fall back to yfinance.info
    if company_facts:
        inputs = from_company_facts(company_facts, market_cap=market_cap)
    elif yfinance_info:
        inputs = from_yfinance_info(yfinance_info)
        if market_cap is not None and inputs.market_cap is None:
            inputs.market_cap = market_cap
    else:
        # No financials available — leave PENDING and don't promote to HARAM
        result = VerificationResult(
            symbol=sym,
            status=ShariaStatus.MIXED,   # conservative default until data lands
            business=biz,
            ratios=None,
            debt_tier=RiskTier.YELLOW,
            cash_tier=RiskTier.YELLOW,
            impermissible_tier=RiskTier.YELLOW,
            overall_tier=RiskTier.YELLOW,
            drift_warning=False,
            notes="No financial data available — pending verification",
            fetched_at=fetched_at,
        )
        if persist:
            _persist(result)
        return result

    ratios = compute(inputs)
    breakdown = derive_status(
        debt_ratio=ratios.debt_ratio,
        cash_ratio=ratios.cash_ratio,
        impermissible_ratio=ratios.impermissible_ratio,
    )

    drift = _check_drift(sym, current_debt_ratio=ratios.debt_ratio)
    overall_tier = worst_tier(
        breakdown.debt_tier, breakdown.cash_tier, breakdown.impermissible_tier
    )
    note_parts = [breakdown.notes]
    if drift:
        note_parts.append("Sharia Drift Radar: rising fast and within 3pp of breach")
    if inputs.notes:
        note_parts.append(f"({inputs.notes})")

    result = VerificationResult(
        symbol=sym,
        status=breakdown.overall,
        business=biz,
        ratios=ratios,
        debt_tier=breakdown.debt_tier,
        cash_tier=breakdown.cash_tier,
        impermissible_tier=breakdown.impermissible_tier,
        overall_tier=overall_tier,
        drift_warning=drift,
        notes=" | ".join(p for p in note_parts if p),
        fetched_at=fetched_at,
    )

    if persist:
        _persist(result)
    return result


def _check_drift(symbol: str, *, current_debt_ratio: float | None) -> bool:
    if current_debt_ratio is None:
        return False
    history = sharia_repo.quarterly_history(symbol, limit=4)
    debt_series = [h["debt_ratio"] for h in history if h.get("debt_ratio") is not None]
    if len(debt_series) < 4:
        return False
    return is_drift_warning(debt_series, current_ratio=current_debt_ratio)


def _persist(result: VerificationResult) -> None:
    """Write the result into financial_ratios_history and update stocks_metadata.

    Skipped silently if the symbol isn't in stocks_metadata — the verifier is
    safe to call on ad-hoc tickers (e.g. user-typed /sharia SYMBOL queries)
    that aren't part of the watchlist.
    """
    if stocks_repo.get(result.symbol) is None:
        return
    try:
        ratios = result.ratios.inputs if result.ratios else None
        sharia_repo.insert_ratios(
            symbol=result.symbol,
            market_cap=ratios.market_cap if ratios else None,
            total_debt=ratios.total_debt if ratios else None,
            interest_bearing_debt=ratios.interest_bearing_debt if ratios else None,
            cash_and_securities=ratios.cash_and_securities if ratios else None,
            total_revenue=ratios.total_revenue if ratios else None,
            impermissible_revenue=ratios.impermissible_revenue if ratios else None,
            debt_ratio=result.ratios.debt_ratio if result.ratios else None,
            cash_ratio=result.ratios.cash_ratio if result.ratios else None,
            impermissible_ratio=result.ratios.impermissible_ratio if result.ratios else None,
            sharia_status=result.status.value,
            risk_tier=result.overall_tier.value if result.overall_tier else None,
            filing_date=ratios.filing_date if ratios else None,
            filing_type=ratios.filing_type if ratios else None,
            notes=result.notes,
            fetched_at=result.fetched_at,
        )
    except Exception as exc:
        log.warning("ratios persist failed", extra={"symbol": result.symbol, "err": str(exc)})

    try:
        stocks_repo.set_sharia_status(
            result.symbol, result.status.value, verified_at=result.fetched_at,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("stocks_metadata update failed",
                    extra={"symbol": result.symbol, "err": str(exc)})
