"""AAOIFI financial ratio computation from yfinance + SEC EDGAR data.

This is the *only* place that derives the three AAOIFI ratios:
  - debt_ratio          = interest-bearing debt / market cap
  - cash_ratio          = (cash + interest-bearing securities) / market cap
  - impermissible_ratio = impermissible income / total revenue

By design, an LLM never produces these numbers. The Sharia officer agent
receives them as structured input and only renders status/explanation.

Inputs are opportunistic — if a field is missing we mark it None and the
tier classifier maps None → YELLOW (conservative warning).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RatioInputs:
    market_cap: float | None
    total_debt: float | None
    interest_bearing_debt: float | None
    cash_and_securities: float | None
    total_revenue: float | None
    impermissible_revenue: float | None
    filing_date: str | None
    filing_type: str | None       # 10-Q | 10-K | derived
    notes: str = ""


@dataclass
class RatiosComputed:
    inputs: RatioInputs
    debt_ratio: float | None
    cash_ratio: float | None
    impermissible_ratio: float | None


def compute(inputs: RatioInputs) -> RatiosComputed:
    debt_ratio = _safe_div(inputs.interest_bearing_debt, inputs.market_cap)
    cash_ratio = _safe_div(inputs.cash_and_securities, inputs.market_cap)
    imp_ratio = _safe_div(inputs.impermissible_revenue, inputs.total_revenue)
    return RatiosComputed(
        inputs=inputs,
        debt_ratio=debt_ratio,
        cash_ratio=cash_ratio,
        impermissible_ratio=imp_ratio,
    )


def _safe_div(num: float | None, denom: float | None) -> float | None:
    if num is None or denom is None or denom <= 0:
        return None
    try:
        return float(num) / float(denom)
    except Exception:
        return None


# --- yfinance / SEC extraction helpers ----------------------------------

def from_yfinance_info(info: dict[str, Any] | None) -> RatioInputs:
    """Best-effort extraction from a yfinance Ticker.info dict.

    yfinance is missing impermissible_revenue (it has no concept of it).
    We default it to 0 with a notes flag so the verifier knows it must be
    overridden from SEC XBRL or marked as unknown.
    """
    if not info:
        return RatioInputs(
            None, None, None, None, None, None, None, None,
            notes="empty yfinance info",
        )

    market_cap = _coerce_float(info.get("marketCap"))
    total_debt = _coerce_float(info.get("totalDebt"))
    cash = _coerce_float(info.get("totalCash"))
    revenue = _coerce_float(info.get("totalRevenue"))

    return RatioInputs(
        market_cap=market_cap,
        total_debt=total_debt,
        interest_bearing_debt=total_debt,    # conservative default — see verifier
        cash_and_securities=cash,
        total_revenue=revenue,
        impermissible_revenue=0.0,           # placeholder; SEC override later
        filing_date=None,
        filing_type="derived",
        notes="yfinance info; impermissible defaulted to 0 (override via SEC)",
    )


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


# --- XBRL extraction (SEC company_facts) ---------------------------------

# US-GAAP concepts we care about (best-effort — not every filer reports the
# exact same names, hence the multi-key fallback).
_DEBT_KEYS = (
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "ShortTermBorrowings",
    "DebtCurrent",
    "InterestBearingDebt",
)
_CASH_KEYS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "MarketableSecuritiesCurrent",
    "ShortTermInvestments",
)
_REVENUE_KEYS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)


def from_company_facts(facts: dict[str, Any] | None,
                       *, market_cap: float | None) -> RatioInputs:
    """Pull the latest reported values for our key concepts from XBRL JSON.

    SEC's companyfacts returns:
      facts.us-gaap.<concept>.units.USD = [ {val, accn, fy, fp, end, ...}, ... ]

    We pick the most recent (highest `end`) entry per concept, sum across the
    interest-bearing debt aliases, and pick the largest revenue across the
    revenue aliases.
    """
    if not facts:
        return RatioInputs(
            market_cap, None, None, None, None, None, None, "company_facts",
            notes="empty facts payload",
        )

    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}

    interest_bearing_debt = _sum_latest(us_gaap, _DEBT_KEYS)
    cash_and_securities = _sum_latest(us_gaap, _CASH_KEYS)
    total_revenue = _max_latest(us_gaap, _REVENUE_KEYS)
    filing_date = _latest_end(us_gaap, _REVENUE_KEYS) or _latest_end(us_gaap, _DEBT_KEYS)

    return RatioInputs(
        market_cap=market_cap,
        total_debt=interest_bearing_debt,
        interest_bearing_debt=interest_bearing_debt,
        cash_and_securities=cash_and_securities,
        total_revenue=total_revenue,
        impermissible_revenue=0.0,            # XBRL has no concept of this
        filing_date=filing_date,
        filing_type="10-Q/10-K (XBRL)",
        notes="extracted from SEC company_facts; impermissible_revenue=0 unless overridden",
    )


def _latest_unit_entry(concept: dict[str, Any] | None) -> dict[str, Any] | None:
    if not concept:
        return None
    units = concept.get("units") or {}
    usd = units.get("USD") or []
    if not usd:
        return None
    return max(usd, key=lambda e: e.get("end") or "")


def _sum_latest(us_gaap: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    total = 0.0
    found = False
    for k in keys:
        entry = _latest_unit_entry(us_gaap.get(k))
        if entry and isinstance(entry.get("val"), (int, float)):
            total += float(entry["val"])
            found = True
    return total if found else None


def _max_latest(us_gaap: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    best = None
    for k in keys:
        entry = _latest_unit_entry(us_gaap.get(k))
        if entry and isinstance(entry.get("val"), (int, float)):
            v = float(entry["val"])
            if best is None or v > best:
                best = v
    return best


def _latest_end(us_gaap: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    best = None
    for k in keys:
        entry = _latest_unit_entry(us_gaap.get(k))
        if entry and entry.get("end"):
            end = str(entry["end"])
            if best is None or end > best:
                best = end
    return best


# US-GAAP / DEI concepts for shares outstanding. dei is the cover-page
# concept (every filer reports it), us-gaap is the financial-statement
# variant (less universally populated). Note: unit is "shares", not "USD".
_SHARES_KEYS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesIssued"),
)


def extract_shares_outstanding(facts: dict[str, Any] | None) -> float | None:
    """Latest shares-outstanding value from SEC companyfacts XBRL.

    Returns None if no concept has a populated `shares` unit array. Used by
    the verifier to compute market_cap = shares × latest_close when the
    yfinance market_cap field is unavailable (e.g. cloud IP rate-limiting).
    """
    if not facts:
        return None
    facts_root = facts.get("facts") or {}
    for ns, key in _SHARES_KEYS:
        block = (facts_root.get(ns) or {}).get(key)
        if not block:
            continue
        rows = (block.get("units") or {}).get("shares") or []
        if not rows:
            continue
        latest = max(rows, key=lambda e: e.get("end") or "")
        val = latest.get("val")
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    return None
