"""Initial watchlist — 51 stocks + 3 halal ETFs.

Each entry carries:
- sector (used by agent_set resolver and dashboard grid)
- btc_beta (rolling sensitivity to BTC; used by BTC Macro agent and heuristic score)
- expected_status (HALAL / MIXED / HARAM seed hint — Sharia officer VERIFIES on first scan)

`expected_status` is *not* authoritative. The Sharia compliance officer recomputes
AAOIFI ratios from real financial data (yfinance + SEC EDGAR) and writes the actual
status into `stocks_metadata.sharia_status`. The hint just speeds up the first scan
and shapes UI defaults until verification completes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class StockSeed:
    symbol: str
    sector: str
    btc_beta: float
    expected_status: str  # "HALAL" | "MIXED" | "HARAM"


def _seed(symbol: str, sector: str, btc_beta: float, expected_status: str) -> StockSeed:
    return StockSeed(symbol=symbol, sector=sector, btc_beta=btc_beta, expected_status=expected_status)


WATCHLIST: Dict[str, StockSeed] = {
    s.symbol: s
    for s in (
        # === BTC-CORRELATED (likely halal — verify ratios) ===
        _seed("MSTR", "BTC_TREASURY", 2.5, "MIXED"),
        _seed("COIN", "CRYPTO_EXCHANGE", 1.8, "HALAL"),
        _seed("MARA", "BTC_MINER", 3.0, "HALAL"),
        _seed("RIOT", "BTC_MINER", 2.8, "HALAL"),
        _seed("CLSK", "BTC_MINER", 2.6, "HALAL"),
        _seed("WULF", "BTC_MINER", 2.5, "HALAL"),
        _seed("CIFR", "BTC_MINER", 2.4, "HALAL"),
        _seed("HUT", "BTC_MINER", 2.3, "HALAL"),
        _seed("BTBT", "BTC_MINER", 2.2, "HALAL"),
        _seed("SMLR", "BTC_TREASURY", 2.0, "HALAL"),
        _seed("SQ", "CRYPTO_ADJACENT", 1.2, "MIXED"),
        _seed("PYPL", "CRYPTO_ADJACENT", 0.6, "MIXED"),
        # === BTC-SENSITIVE TECH ===
        _seed("TSLA", "BTC_TREASURY", 0.7, "HALAL"),
        _seed("NVDA", "MINING_HARDWARE", 0.5, "HALAL"),
        _seed("AMD", "MINING_HARDWARE", 0.4, "HALAL"),
        # === TECH MEGA-CAPS ===
        _seed("AAPL", "TECH_MEGA", 0.3, "HALAL"),
        _seed("MSFT", "TECH_MEGA", 0.2, "HALAL"),
        _seed("GOOGL", "TECH_MEGA", 0.3, "HALAL"),
        _seed("AMZN", "TECH_MEGA", 0.2, "MIXED"),
        _seed("META", "TECH_MEGA", 0.4, "MIXED"),
        _seed("AVGO", "SEMICONDUCTORS", 0.3, "HALAL"),
        _seed("ORCL", "TECH_LARGE", 0.1, "HALAL"),
        _seed("CRM", "TECH_LARGE", 0.2, "HALAL"),
        _seed("NFLX", "TECH_LARGE", 0.2, "MIXED"),
        _seed("INTC", "SEMICONDUCTORS", 0.2, "HALAL"),
        _seed("QCOM", "SEMICONDUCTORS", 0.2, "HALAL"),
        _seed("ADBE", "TECH_LARGE", 0.1, "HALAL"),
        # === PAYMENT (mixed — verify) ===
        _seed("V", "FINANCE_PAYMENT", 0.0, "MIXED"),
        _seed("MA", "FINANCE_PAYMENT", 0.0, "MIXED"),
        # === HEALTHCARE ===
        _seed("UNH", "HEALTHCARE_INSURE", 0.0, "HALAL"),
        _seed("JNJ", "HEALTHCARE_PHARMA", 0.0, "HALAL"),
        _seed("LLY", "HEALTHCARE_PHARMA", 0.0, "HALAL"),
        _seed("PFE", "HEALTHCARE_PHARMA", 0.0, "HALAL"),
        _seed("ABBV", "HEALTHCARE_PHARMA", 0.0, "HALAL"),
        _seed("MRK", "HEALTHCARE_PHARMA", 0.0, "HALAL"),
        _seed("TMO", "HEALTHCARE_DEVICE", 0.0, "HALAL"),
        # === CONSUMER ===
        _seed("WMT", "CONSUMER_RETAIL", 0.0, "MIXED"),
        _seed("COST", "CONSUMER_RETAIL", 0.0, "MIXED"),
        _seed("HD", "CONSUMER_RETAIL", 0.0, "HALAL"),
        _seed("NKE", "CONSUMER_DISC", 0.1, "HALAL"),
        _seed("MCD", "CONSUMER_DISC", 0.0, "MIXED"),
        _seed("SBUX", "CONSUMER_DISC", 0.0, "MIXED"),
        _seed("DIS", "CONSUMER_DISC", 0.1, "MIXED"),
        _seed("KO", "CONSUMER_STAPLES", 0.0, "HALAL"),
        _seed("PEP", "CONSUMER_STAPLES", 0.0, "HALAL"),
        _seed("PG", "CONSUMER_STAPLES", 0.0, "HALAL"),
        # === ENERGY/INDUSTRIAL ===
        _seed("XOM", "ENERGY", 0.0, "HALAL"),
        _seed("CVX", "ENERGY", 0.0, "HALAL"),
        _seed("CAT", "INDUSTRIAL", 0.0, "HALAL"),
        _seed("BA", "INDUSTRIAL", 0.0, "HALAL"),
        _seed("GE", "INDUSTRIAL", 0.0, "HALAL"),
        # === HALAL ETF ALTERNATIVES ===
        _seed("HLAL", "HALAL_ETF", 0.1, "HALAL"),
        _seed("SPUS", "HALAL_ETF", 0.1, "HALAL"),
        _seed("SPSK", "HALAL_SUKUK", 0.0, "HALAL"),
    )
}


def all_symbols() -> list[str]:
    return list(WATCHLIST.keys())


def get_seed(symbol: str) -> StockSeed | None:
    return WATCHLIST.get(symbol.upper())
