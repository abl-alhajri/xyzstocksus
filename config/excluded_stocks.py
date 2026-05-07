"""Hard-excluded tickers (auto-haram by business activity per AAOIFI Standard 21).

These are explicitly disqualified from the watchlist before any financial-ratio
screening runs. Any attempt to /enable or /analyze them is rejected with the
exclusion reason.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exclusion:
    symbol: str
    reason: str
    category: str  # one of: BANK, INSURANCE, INTEREST_CREDIT, BTC_FUTURES_ETF, MIXED_ETF


EXCLUDED: dict[str, Exclusion] = {
    e.symbol: e
    for e in (
        # Conventional banking — interest is core revenue
        Exclusion("JPM", "Conventional bank — interest-bearing revenue is core", "BANK"),
        Exclusion("BAC", "Conventional bank — interest-bearing revenue is core", "BANK"),
        Exclusion("WFC", "Conventional bank — interest-bearing revenue is core", "BANK"),
        Exclusion("GS", "Investment bank — interest/derivatives heavy", "BANK"),
        Exclusion("MS", "Investment bank — interest/derivatives heavy", "BANK"),
        # Interest-based credit
        Exclusion("AXP", "Interest-based credit lender", "INTEREST_CREDIT"),
        Exclusion("HOOD", "Margin lending + interest income", "INTEREST_CREDIT"),
        Exclusion("SOFI", "Online bank — interest revenue", "BANK"),
        # BTC futures ETFs — gharar (excessive uncertainty)
        Exclusion("IBIT", "BTC futures-based ETF — gharar concerns", "BTC_FUTURES_ETF"),
        Exclusion("FBTC", "BTC futures-based ETF — gharar concerns", "BTC_FUTURES_ETF"),
        Exclusion("GBTC", "BTC trust with interest-bearing structure", "BTC_FUTURES_ETF"),
        Exclusion("BITO", "BTC futures-based ETF — gharar concerns", "BTC_FUTURES_ETF"),
        # Broad-market ETFs containing banks/non-compliant holdings
        Exclusion("SPY", "Broad ETF holds banks and non-compliant constituents", "MIXED_ETF"),
        Exclusion("QQQ", "Broad ETF holds non-compliant constituents", "MIXED_ETF"),
        Exclusion("IWM", "Broad ETF holds banks and non-compliant constituents", "MIXED_ETF"),
        Exclusion("DIA", "Broad ETF holds banks and non-compliant constituents", "MIXED_ETF"),
        Exclusion("VTI", "Broad ETF holds banks and non-compliant constituents", "MIXED_ETF"),
    )
}


def is_excluded(symbol: str) -> bool:
    return symbol.upper() in EXCLUDED


def exclusion_for(symbol: str) -> Exclusion | None:
    return EXCLUDED.get(symbol.upper())
