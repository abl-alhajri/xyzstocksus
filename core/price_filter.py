"""Price-band filter — skip tickers outside the configured [min, max] range.

Applied in `core.orchestrator` after the price batch returns and before
heuristic scoring runs. Tickers whose last close is None pass through
(missing-data is logged elsewhere; we never silently drop on a fetch failure).
"""
from __future__ import annotations

from config.settings import settings


def is_in_range(price: float | None,
                *,
                min_usd: float | None = None,
                max_usd: float | None = None) -> bool:
    """Return True iff `price` is within [min_usd, max_usd] (inclusive).

    None price → True (don't drop on a missing fetch — the scan loop will skip
    on its own when there is no DataFrame). Overrides exposed for tests.
    """
    if price is None:
        return True
    lo = settings.min_stock_price_usd if min_usd is None else min_usd
    hi = settings.max_stock_price_usd if max_usd is None else max_usd
    return lo <= price <= hi


def reason_out_of_range(price: float,
                        *,
                        min_usd: float | None = None,
                        max_usd: float | None = None) -> str:
    lo = settings.min_stock_price_usd if min_usd is None else min_usd
    hi = settings.max_stock_price_usd if max_usd is None else max_usd
    return f"${price:.2f} outside [${lo:.2f}, ${hi:.2f}] range"
