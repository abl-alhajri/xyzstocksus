"""
Smart yfinance wrapper for XYZStocksUS bot.

Fixes the 429 rate limit issue by:
1. BATCH downloads (1 request for N tickers, not N requests)
2. In-memory cache with TTL (avoid re-fetching same data)
3. Exponential backoff with jitter (smart retry on 429)
4. Semaphore (limit concurrent requests)
5. Stooq fallback (free alternative when Yahoo is fully blocked)

Drop-in replacement: import this module instead of yfinance directly.

USAGE:
    from data_fetcher import get_prices, get_price

    # Batch (preferred) — one network call for all tickers
    data = get_prices(['AAPL', 'MSFT', 'NVDA'], period='5d')
    aapl_df = data['AAPL']

    # Single ticker
    df = get_price('AAPL', period='1d')
"""

import logging
import random
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────
CACHE_TTL_SECONDS = 300              # 5 minutes — prices don't change that fast intraday
MAX_RETRIES = 4                      # 4 attempts with backoff
MAX_CONCURRENT_REQUESTS = 2          # never hammer Yahoo
BATCH_SIZE = 20                      # split big lists into chunks of 20
INTER_BATCH_DELAY = 1.5              # seconds between batches
USER_AGENTS = [                      # rotate to look less bot-like
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


# ─── Cache ────────────────────────────────────────────────────────
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
_request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)


# ─── Helpers ──────────────────────────────────────────────────────
def _is_rate_limit_error(err: Exception) -> bool:
    """Detect 429 / rate-limit errors from yfinance."""
    s = str(err).lower()
    return any(marker in s for marker in [
        "429", "too many requests", "rate limit",
        "expecting value",  # yfinance returns empty body when blocked
    ])


def _backoff_sleep(attempt: int):
    """Exponential backoff with jitter: 2s → 4s → 8s → 16s, ± random."""
    base = 2 ** (attempt + 1)
    jitter = random.uniform(0, 2)
    wait = base + jitter
    logger.info(f"[fetcher] Backoff sleep {wait:.1f}s (attempt {attempt + 1})")
    time.sleep(wait)


def _set_user_agent():
    """Rotate user-agent on each batch — helps with 429."""
    try:
        import yfinance.shared as shared
        ua = random.choice(USER_AGENTS)
        if hasattr(shared, "_REQUESTS_HEADERS"):
            shared._REQUESTS_HEADERS["User-Agent"] = ua
    except Exception:
        pass  # not critical


def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]


# ─── Core fetch ───────────────────────────────────────────────────
def _fetch_chunk(
    tickers: List[str],
    period: str,
    interval: str,
) -> Dict[str, pd.DataFrame]:
    """
    Fetch ONE chunk via yf.download() with retry.
    Returns {ticker: DataFrame} for tickers that succeeded.
    """
    result: Dict[str, pd.DataFrame] = {}

    for attempt in range(MAX_RETRIES):
        try:
            with _request_semaphore:
                _set_user_agent()

                df = yf.download(
                    tickers=tickers,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    threads=False,         # don't spawn threads inside yfinance
                    progress=False,
                    auto_adjust=False,     # preserve raw OHLC — indicators expect unadjusted
                    timeout=15,
                )

            if df is None or df.empty:
                logger.warning(f"[fetcher] Empty response for {tickers}")
                if attempt < MAX_RETRIES - 1:
                    _backoff_sleep(attempt)
                    continue
                return result

            # Normalise: single ticker returns flat columns; many tickers return MultiIndex
            if len(tickers) == 1:
                t = tickers[0]
                clean = df.dropna(how="all")
                if not clean.empty:
                    result[t] = clean
            else:
                for t in tickers:
                    try:
                        sub = df[t].dropna(how="all")
                        if not sub.empty:
                            result[t] = sub
                    except (KeyError, ValueError):
                        logger.warning(f"[fetcher] No data column for {t}")

            return result

        except Exception as e:
            if _is_rate_limit_error(e):
                logger.warning(
                    f"[fetcher] Rate limited (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                )
                if attempt < MAX_RETRIES - 1:
                    _backoff_sleep(attempt)
                    continue
                logger.error(f"[fetcher] Giving up on {tickers} after {MAX_RETRIES} retries")
                return result
            else:
                logger.error(f"[fetcher] Non-retry error for {tickers}: {e}")
                return result

    return result


# ─── Stooq fallback (free, no rate limit) ─────────────────────────
def _stooq_fallback(ticker: str, period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fallback to Stooq when Yahoo is fully blocked.
    Stooq is free, no API key, no aggressive rate limiting.
    Only handles US stocks (append .US for crypto/futures need different handling).
    """
    try:
        import pandas_datareader.data as pdr
        from datetime import timedelta

        # rough period→days mapping
        days = {"1d": 2, "5d": 7, "1mo": 35, "3mo": 100, "1y": 380}.get(period, 7)
        end = datetime.now()
        start = end - timedelta(days=days)

        # Stooq uses lowercase + .us suffix for US stocks
        symbol = ticker.lower()
        if not any(c in symbol for c in ["-", "."]):
            symbol = f"{symbol}.us"

        df = pdr.DataReader(symbol, "stooq", start, end)
        if df is not None and not df.empty:
            df = df.sort_index()  # Stooq returns descending
            logger.info(f"[fetcher] Stooq fallback succeeded for {ticker}")
            return df
    except Exception as e:
        logger.warning(f"[fetcher] Stooq fallback failed for {ticker}: {e}")

    return None


# ─── Public API ───────────────────────────────────────────────────
def get_prices(
    tickers: List[str],
    period: str = "5d",
    interval: str = "1d",
    use_fallback: bool = True,
) -> Dict[str, pd.DataFrame]:
    """
    Fetch prices for many tickers — SMART version.

    - Returns cached data when fresh
    - Batches the rest into chunks of BATCH_SIZE
    - Sleeps between batches to avoid 429
    - Falls back to Stooq for any tickers that still fail
    """
    if not tickers:
        return {}

    result: Dict[str, pd.DataFrame] = {}

    # 1) cache pass
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
        f"[fetcher] Cache: {len(result)} hit, {len(misses)} miss → fetching in chunks of {BATCH_SIZE}"
    )

    # 2) batched network pass
    for i, chunk in enumerate(_chunk(misses, BATCH_SIZE)):
        if i > 0:
            time.sleep(INTER_BATCH_DELAY)  # be nice to Yahoo

        chunk_result = _fetch_chunk(chunk, period, interval)
        for t, df in chunk_result.items():
            result[t] = df
            _cache.set(t, period, interval, df)

    # 3) fallback for whatever Yahoo refused
    if use_fallback:
        still_missing = [t for t in misses if t not in result]
        if still_missing:
            logger.warning(
                f"[fetcher] {len(still_missing)} tickers missing after Yahoo, trying Stooq fallback"
            )
            for t in still_missing:
                df = _stooq_fallback(t, period)
                if df is not None:
                    result[t] = df
                    _cache.set(t, period, interval, df)

    success = len(result)
    total = len(tickers)
    logger.info(f"[fetcher] Done: {success}/{total} tickers retrieved")

    return result


def get_price(ticker: str, period: str = "5d", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Convenience wrapper for a single ticker."""
    return get_prices([ticker], period=period, interval=interval).get(ticker)


def cache_stats() -> dict:
    """For /debug command if you want it."""
    return _cache.stats()


def clear_cache():
    """Force fresh fetch on next call."""
    global _cache
    _cache = _Cache(CACHE_TTL_SECONDS)
    logger.info("[fetcher] Cache cleared")
