"""Tiingo-primary OHLCV fetcher with Yahoo per-ticker fallback.

Tiingo works reliably from cloud IPs (unlike Yahoo, which blocks Railway's
shared egress). Yahoo is kept as a per-ticker secondary for tickers Tiingo
doesn't cover and for local dev environments without a Tiingo key.

Free-tier (Tiingo Power): 1000 req/hr, 50 unique symbols/day. We serialize
calls with ~1.2s gaps so a cold-cache 50-ticker scan finishes in ~60s and
stays well under the hourly cap. The 5-min in-memory cache absorbs repeat
calls within a scan window.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


# --- Configuration ----------------------------------------------------------
CACHE_TTL_SECONDS = 300
INTER_REQUEST_DELAY = 1.2
TIINGO_TIMEOUT = 15
TIINGO_BASE = "https://api.tiingo.com/tiingo"
MAX_RETRIES = 3

_PERIOD_DAYS = {
    "1d": 2, "5d": 7, "1mo": 35, "3mo": 100,
    "6mo": 200, "1y": 380, "2y": 740,
    "60d": 70, "120d": 140,
}
_INTERVAL_FREQ = {"1d": "daily", "1wk": "weekly", "1mo": "monthly"}


TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY")
RAILWAY_ENV = os.environ.get("RAILWAY_ENVIRONMENT")

if RAILWAY_ENV and not TIINGO_API_KEY:
    raise RuntimeError(
        "TIINGO_API_KEY is required in production. "
        "Set it in Railway env vars."
    )
elif not TIINGO_API_KEY:
    logger.warning(
        "TIINGO_API_KEY not set — Tiingo fetch disabled, relying on Yahoo fallback"
    )


# --- Cache (unchanged) ------------------------------------------------------
class _Cache:
    """Thread-safe TTL cache."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._store: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _key(self, ticker: str, period: str, interval: str) -> str:
        return f"{ticker}|{period}|{interval}"

    def get(self, ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
        k = self._key(ticker, period, interval)
        with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return None
            df, ts = entry
            if (time.time() - ts) > self.ttl:
                del self._store[k]
                return None
            return df.copy()

    def set(self, ticker: str, period: str, interval: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        k = self._key(ticker, period, interval)
        with self._lock:
            self._store[k] = (df.copy(), time.time())

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._store), "ttl": self.ttl}


_cache = _Cache(CACHE_TTL_SECONDS)
_request_semaphore = threading.Semaphore(1)


# --- Symbol classification --------------------------------------------------
def _is_crypto(ticker: str) -> bool:
    """Detect crypto-style tickers we should route to Tiingo's crypto endpoint."""
    t = ticker.upper().strip()
    return t.endswith("-USD") and t not in ("EUR-USD", "GBP-USD", "JPY-USD")


def _to_tiingo_crypto_symbol(ticker: str) -> str:
    """BTC-USD → btcusd, ETH-USD → ethusd."""
    return ticker.lower().replace("-", "")


# --- Tiingo: stock candles --------------------------------------------------
def _tiingo_fetch_stock(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    if not TIINGO_API_KEY:
        return None
    freq = _INTERVAL_FREQ.get(interval, "daily")
    days = _PERIOD_DAYS.get(period, 7)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"{TIINGO_BASE}/daily/{ticker.upper()}/prices"
    params = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "resampleFreq": freq,
        "token": TIINGO_API_KEY,
        "format": "json",
    }
    return _tiingo_request(url, params, ticker, kind="stock")


# --- Tiingo: crypto candles -------------------------------------------------
def _tiingo_fetch_crypto(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    if not TIINGO_API_KEY:
        return None
    # Tiingo crypto supports 1day / 4hour / 1hour; the bot only uses daily.
    freq = "1day"
    days = _PERIOD_DAYS.get(period, 7)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"{TIINGO_BASE}/crypto/prices"
    params = {
        "tickers": _to_tiingo_crypto_symbol(ticker),
        "startDate": start.isoformat(),
        "resampleFreq": freq,
        "token": TIINGO_API_KEY,
    }
    return _tiingo_request(url, params, ticker, kind="crypto")


# --- Tiingo HTTP wrapper ----------------------------------------------------
def _tiingo_request(url: str, params: dict, ticker: str, kind: str) -> Optional[pd.DataFrame]:
    headers = {"Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        try:
            with _request_semaphore:
                r = requests.get(url, params=params, headers=headers, timeout=TIINGO_TIMEOUT)
        except requests.exceptions.RequestException as e:
            logger.warning(f"[fetcher] Tiingo network error for {ticker} (attempt {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            return None

        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError as e:
                logger.warning(f"[fetcher] Tiingo JSON parse failed for {ticker}: {e}")
                return None
            return _shape_tiingo_response(data, ticker, kind)

        if r.status_code == 429:
            logger.warning(f"[fetcher] Tiingo 429 for {ticker} (attempt {attempt+1}/{MAX_RETRIES})")
            time.sleep(2 ** attempt + 1)
            continue
        if r.status_code == 404:
            logger.info(f"[fetcher] Tiingo 404 for {ticker} (unsupported symbol)")
            return None
        logger.warning(f"[fetcher] Tiingo {r.status_code} for {ticker}: {r.text[:200]}")
        return None
    return None


def _shape_tiingo_response(data, ticker: str, kind: str) -> Optional[pd.DataFrame]:
    """Coerce Tiingo JSON to an OHLCV DataFrame matching yfinance column casing."""
    if kind == "stock":
        rows = data
    else:  # crypto: [{"ticker":..., "priceData":[...]}]
        if not data:
            return None
        rows = data[0].get("priceData", [])

    if not rows:
        logger.info(f"[fetcher] Tiingo empty for {ticker}")
        return None

    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        logger.warning(f"[fetcher] Tiingo response missing 'date' for {ticker}")
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    if not keep or "Close" not in keep:
        return None
    return df[keep]


# --- Yahoo per-ticker fallback ----------------------------------------------
def _yahoo_fallback_one(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    """Single-ticker yfinance call. Used only when Tiingo can't serve a symbol."""
    try:
        import yfinance as yf
        df = yf.download(
            tickers=ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
            timeout=15,
        )
        if df is None or df.empty:
            return None
        clean = df.dropna(how="all")
        if clean.empty:
            return None
        if isinstance(clean.columns, pd.MultiIndex):
            clean.columns = clean.columns.get_level_values(0)
        return clean
    except Exception as e:
        logger.warning(f"[fetcher] Yahoo fallback failed for {ticker}: {e}")
        return None


# --- Public API -------------------------------------------------------------
def get_prices(
    tickers: List[str],
    period: str = "5d",
    interval: str = "1d",
    use_fallback: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Fetch OHLCV for many tickers. Tiingo primary, Yahoo per-ticker fallback."""
    if not tickers:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    misses: List[str] = []

    for t in tickers:
        cached = _cache.get(t, period, interval)
        if cached is not None:
            result[t] = cached
        else:
            misses.append(t)

    if not misses:
        logger.info(f"[fetcher] Full cache hit ({len(tickers)} tickers)")
        return result

    logger.info(
        f"[fetcher] Cache: {len(result)} hit, {len(misses)} miss → fetching from Tiingo"
    )

    tiingo_failed: List[str] = []
    for i, t in enumerate(misses):
        if i > 0:
            time.sleep(INTER_REQUEST_DELAY)
        df = (_tiingo_fetch_crypto if _is_crypto(t) else _tiingo_fetch_stock)(t, period, interval)
        if df is not None and not df.empty:
            result[t] = df
            _cache.set(t, period, interval, df)
        else:
            tiingo_failed.append(t)

    if use_fallback and tiingo_failed:
        logger.info(
            f"[fetcher] {len(tiingo_failed)} tickers missing from Tiingo, trying Yahoo"
        )
        for t in tiingo_failed:
            df = _yahoo_fallback_one(t, period, interval)
            if df is not None and not df.empty:
                result[t] = df
                _cache.set(t, period, interval, df)

    logger.info(f"[fetcher] Done: {len(result)}/{len(tickers)} tickers retrieved")
    return result


def get_price(ticker: str, period: str = "5d", interval: str = "1d") -> Optional[pd.DataFrame]:
    return get_prices([ticker], period=period, interval=interval).get(ticker)


def cache_stats() -> dict:
    return _cache.stats()


def clear_cache():
    global _cache
    _cache = _Cache(CACHE_TTL_SECONDS)
    logger.info("[fetcher] Cache cleared")
