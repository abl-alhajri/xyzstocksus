"""yfinance price fetching — delegates to core.data_fetcher for batching,
retries, and Stooq fallback. Keeps `fetch_history` signature + FetchResult
shape so the rest of the codebase is unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from core import cache
from core.logger import get_logger

log = get_logger("data.prices")


# --- public types --------------------------------------------------------

@dataclass
class FetchResult:
    frames: dict[str, "object"]   # pandas.DataFrame at runtime
    failed: list[str]
    fetched_at: str


class YFinanceUnavailable(RuntimeError):
    pass


def fetch_history(
    symbols: Iterable[str],
    *,
    period: str = "60d",
    interval: str = "1d",
    use_cache: bool = True,
) -> FetchResult:
    """Batch-download OHLCV history for multiple tickers.

    Returns a FetchResult with one DataFrame per successfully fetched symbol.
    Failures are logged and recorded in `failed`; we never raise — graceful
    degradation is required so a single bad ticker does not abort the scan.
    """
    syms = sorted({s.upper() for s in symbols if s})
    if not syms:
        return FetchResult(frames={}, failed=[], fetched_at=_now())

    cache_key = f"{period}|{interval}|{','.join(syms)}"
    if use_cache:
        cached = cache.get("prices", cache_key, cache.TTL_PRICES_INTRA)
        if cached is not None:
            return cached

    try:
        from core.data_fetcher import get_prices
    except ImportError as exc:
        log.warning("data_fetcher unavailable", extra={"err": str(exc)})
        return FetchResult(frames={}, failed=list(syms), fetched_at=_now())

    try:
        frames_dict = get_prices(syms, period=period, interval=interval)
    except Exception as exc:
        log.error("price batch download failed",
                  extra={"err": str(exc), "symbols": syms})
        return FetchResult(frames={}, failed=list(syms), fetched_at=_now())

    frames: dict[str, object] = {sym: df for sym, df in frames_dict.items()}
    failed: list[str] = [s for s in syms if s not in frames]

    result = FetchResult(frames=frames, failed=failed, fetched_at=_now())
    if use_cache and frames:
        try:
            cache.set_("prices", cache_key, result)
        except Exception as exc:  # pragma: no cover
            log.warning("price cache write failed", extra={"err": str(exc)})

    if failed:
        log.warning("price partial failure",
                   extra={"failed": failed, "ok": list(frames.keys())})
    return result


def latest_close(symbol: str) -> float | None:
    """Convenience: return the most recent daily close for a single symbol."""
    res = fetch_history([symbol], period="5d", interval="1d")
    df = res.frames.get(symbol.upper())
    if df is None or df.empty:
        return None
    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
