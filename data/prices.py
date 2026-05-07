"""yfinance price fetching with rate-limiting, retries, and graceful degradation.

`fetch_history` returns a pandas DataFrame per symbol; missing/erroring symbols
are reported in the second item of the tuple so callers can decide what to do.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from core import cache
from core.logger import get_logger

log = get_logger("data.prices")

# yfinance, pandas, tenacity are heavy/brittle. They're imported lazily inside
# functions so this module imports cleanly even without them installed (unit
# tests import it for the rate-limiter logic and never hit the network).
def _lazy_yf():
    try:
        import yfinance as yf  # type: ignore
        return yf
    except ImportError:
        return None


# --- rate limiter (5 req/sec) -------------------------------------------

_RATE_LOCK = threading.Lock()
_RATE_WINDOW = 1.0  # seconds
_RATE_MAX = 5
_REQUEST_TIMES: list[float] = []


def _rate_gate() -> None:
    """Block until at most _RATE_MAX requests have happened in the last second."""
    with _RATE_LOCK:
        now = time.monotonic()
        cutoff = now - _RATE_WINDOW
        while _REQUEST_TIMES and _REQUEST_TIMES[0] < cutoff:
            _REQUEST_TIMES.pop(0)
        if len(_REQUEST_TIMES) >= _RATE_MAX:
            sleep_for = _RATE_WINDOW - (now - _REQUEST_TIMES[0]) + 0.01
            time.sleep(max(sleep_for, 0))
            now = time.monotonic()
            cutoff = now - _RATE_WINDOW
            while _REQUEST_TIMES and _REQUEST_TIMES[0] < cutoff:
                _REQUEST_TIMES.pop(0)
        _REQUEST_TIMES.append(time.monotonic())


# --- public types --------------------------------------------------------

@dataclass
class FetchResult:
    frames: dict[str, "object"]   # pandas.DataFrame at runtime
    failed: list[str]
    fetched_at: str


# --- batched fetch with retry -------------------------------------------

class YFinanceUnavailable(RuntimeError):
    pass


def _yf_download(symbols: list[str], period: str, interval: str):
    """Wrapped with tenacity at call time so the dependency stays lazy."""
    yf = _lazy_yf()
    if yf is None:
        raise YFinanceUnavailable("yfinance not installed")
    from tenacity import (  # type: ignore
        retry, stop_after_attempt, wait_exponential, retry_if_exception_type,
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _do():
        _rate_gate()
        return yf.download(
            tickers=" ".join(symbols),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=False,
            threads=False,
            progress=False,
        )

    return _do()


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

    frames: dict[str, object] = {}
    failed: list[str] = []

    try:
        df = _yf_download(syms, period=period, interval=interval)
    except YFinanceUnavailable:
        log.warning("yfinance not installed", extra={"symbols": syms})
        return FetchResult(frames={}, failed=list(syms), fetched_at=_now())
    except Exception as exc:
        log.error("yfinance batch download failed",
                  extra={"err": str(exc), "symbols": syms})
        return FetchResult(frames={}, failed=list(syms), fetched_at=_now())

    import pandas as pd  # type: ignore
    if isinstance(df.columns, pd.MultiIndex):
        for sym in syms:
            try:
                sub = df[sym].dropna(how="all")
                if sub.empty:
                    failed.append(sym)
                else:
                    frames[sym] = sub
            except KeyError:
                failed.append(sym)
    else:
        # Single-symbol download has flat columns
        if df.empty:
            failed = list(syms)
        else:
            frames[syms[0]] = df.dropna(how="all")

    result = FetchResult(frames=frames, failed=failed, fetched_at=_now())
    if use_cache and frames:
        try:
            cache.set_("prices", cache_key, result)
        except Exception as exc:  # pragma: no cover
            log.warning("price cache write failed", extra={"err": str(exc)})

    if failed:
        log.warning("yfinance partial failure",
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
