"""Business activity screen — first AAOIFI gate before any financial ratios.

Two layers:
1. Hard exclusion list (config.excluded_stocks) — auto-haram, no override.
2. SIC-code / sector heuristic — flags suspect industries (alcohol, gambling,
   tobacco, defence, conventional banking) when SEC sector data is available.

This module returns a verdict object; `verifier.py` calls it before computing
financial ratios. If business screen fails → status is HARAM and we skip the
expensive XBRL parse entirely.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.excluded_stocks import EXCLUDED


# SIC code prefixes (4-digit) flagged as non-compliant business activities.
# Source: SEC SIC list. We block at the prefix level for safety.
NON_COMPLIANT_SIC: dict[str, str] = {
    # Banks & savings institutions
    "6020": "Conventional commercial bank",
    "6021": "National commercial bank",
    "6022": "State commercial bank",
    "6029": "Commercial bank n.e.c.",
    "6035": "Savings institution, federal",
    "6036": "Savings institution, state",
    "6099": "Functions related to depository banking",
    # Securities brokers / dealers (mostly margin-based)
    "6199": "Finance services (margin / interest)",
    "6211": "Security brokers / dealers",
    # Insurance (conventional)
    "6311": "Life insurance",
    "6321": "Accident and health insurance",
    "6331": "Fire, marine, casualty insurance",
    # Alcohol
    "2080": "Beverages — alcohol",
    "2082": "Malt beverages",
    "2084": "Wines / brandy",
    "2085": "Distilled spirits",
    # Tobacco
    "2100": "Tobacco products",
    "2111": "Cigarettes",
    # Gambling
    "7011": "Hotels / casinos (verify revenue mix)",
    "7993": "Coin-operated amusement (gambling devices)",
    "7990": "Services — amusement / gambling",
    # Adult entertainment / similar
    "7841": "Video tape rental (verify content)",
    # Weapons
    "3480": "Ordnance / accessories",
    "3482": "Small arms ammunition",
    "3483": "Ammunition (except small arms)",
    "3484": "Small arms",
    "3489": "Ordnance / accessories n.e.c.",
}


@dataclass
class BusinessVerdict:
    passed: bool
    reason: str | None
    category: str | None  # "HARD_EXCLUDED" | "SIC_BLOCK" | "SECTOR_HINT" | None
    notes: str = ""


def _sic_prefix(sic: str | int | None) -> str | None:
    if sic is None:
        return None
    return str(sic).strip()[:4] or None


def screen(
    *,
    symbol: str,
    sec_sic: str | int | None = None,
    yfinance_industry: str | None = None,
) -> BusinessVerdict:
    """Run the business activity screen.

    Returns BusinessVerdict.passed=True when the symbol is acceptable for the
    financial-ratio stage, False when it must be classified HARAM up-front.
    """
    sym = symbol.upper()

    # 1. Hard exclusion list
    if sym in EXCLUDED:
        e = EXCLUDED[sym]
        return BusinessVerdict(
            passed=False,
            reason=e.reason,
            category="HARD_EXCLUDED",
            notes=f"Pre-listed exclusion category: {e.category}",
        )

    # 2. SIC-based block
    sic_pref = _sic_prefix(sec_sic)
    if sic_pref and sic_pref in NON_COMPLIANT_SIC:
        return BusinessVerdict(
            passed=False,
            reason=NON_COMPLIANT_SIC[sic_pref],
            category="SIC_BLOCK",
            notes=f"SIC {sic_pref} flagged as non-compliant",
        )

    # 3. Industry-name hint (best-effort, not authoritative)
    if yfinance_industry:
        ind = yfinance_industry.lower()
        flags = (
            ("bank", "Conventional banking activity"),
            ("insurance", "Conventional insurance"),
            ("alcohol", "Alcohol production"),
            ("brewer", "Alcohol production"),
            ("distill", "Alcohol production"),
            ("tobacco", "Tobacco production"),
            ("gambling", "Gambling / casinos"),
            ("casino", "Gambling / casinos"),
            ("weapon", "Offensive weapons"),
            ("defense", "Defence — verify offensive vs defensive split"),
        )
        for keyword, reason in flags:
            if keyword in ind:
                return BusinessVerdict(
                    passed=False,
                    reason=reason,
                    category="SECTOR_HINT",
                    notes=f"yfinance industry='{yfinance_industry}' matched '{keyword}'",
                )

    return BusinessVerdict(passed=True, reason=None, category=None,
                           notes="Business screen passed")
