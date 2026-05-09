"""Sharia-certified ETFs — bypass the financial-ratio pipeline.

These ETFs are pre-screened and certified Halal by recognized Sharia
boards (Wahed Invest's HLAL, SP Funds' SPUS/SPSK). Running our generic
business-screen + AAOIFI-ratio pipeline against them is meaningless —
the underlying fund composition is the only thing that matters and it's
already vetted by the issuer's Sharia board.

When `is_certified_etf(sym)` is True, sharia.verifier.verify() short-
circuits to HALAL/GREEN before touching SEC EDGAR or Tiingo.
"""
from __future__ import annotations

CERTIFIED_ETFS: dict[str, str] = {
    "HLAL": "Wahed FTSE USA Shariah ETF (Wahed Invest)",
    "SPUS": "SP Funds S&P 500 Sharia Industry Exclusions ETF",
    "SPSK": "SP Funds Dow Jones Global Sukuk ETF",
}


def is_certified_etf(symbol: str) -> bool:
    return symbol.upper() in CERTIFIED_ETFS


def issuer_for(symbol: str) -> str | None:
    return CERTIFIED_ETFS.get(symbol.upper())
