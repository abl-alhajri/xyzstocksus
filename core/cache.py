"""File-backed TTL cache for data fetches.

Why file-backed: the worker and web processes don't share memory, but they do
share the /data volume. Anything that hits an external API (yfinance, Coinbase,
Fed RSS, Trump RSS, SEC EDGAR) goes through here so we don't double-fetch.

TTLs (per spec):
- heuristic     30 min
- news          30 min
- macro         6 hours
- prescreen     30 min
- sharia        90 days (refresh on new filing)
- prices_intra  10 min
- btc_intra     1 min
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any

from config.settings import settings, ensure_dirs

# Centralised TTLs in seconds, named so callers don't sprinkle magic numbers
TTL_HEURISTIC = 30 * 60
TTL_NEWS = 30 * 60
TTL_MACRO = 6 * 3600
TTL_PRESCREEN = 30 * 60
TTL_SHARIA = 90 * 24 * 3600
TTL_PRICES_INTRA = 10 * 60
TTL_BTC_INTRA = 60
TTL_EARNINGS = 6 * 3600
TTL_SEC_FILINGS = 6 * 3600


def _key_to_path(namespace: str, key: str) -> Path:
    h = hashlib.sha1(f"{namespace}|{key}".encode("utf-8")).hexdigest()
    sub = h[:2]
    ensure_dirs()
    folder = settings.cache_dir / namespace / sub
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{h}.pkl"


def get(namespace: str, key: str, ttl: int) -> Any | None:
    """Return cached value if fresh, else None."""
    path = _key_to_path(namespace, key)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > ttl:
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        return None


def set_(namespace: str, key: str, value: Any) -> None:
    """Store a value under (namespace, key). Atomic via tmp+rename."""
    path = _key_to_path(namespace, key)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def invalidate(namespace: str, key: str) -> None:
    path = _key_to_path(namespace, key)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def get_or_compute(namespace: str, key: str, ttl: int, compute):
    """Read-through cache: return cached value or call `compute()` and store."""
    cached = get(namespace, key, ttl)
    if cached is not None:
        return cached
    fresh = compute()
    if fresh is not None:
        set_(namespace, key, fresh)
    return fresh


def stats() -> dict:
    """Total file count + size per namespace, for /status and /cost diagnostics."""
    out: dict = {}
    if not settings.cache_dir.exists():
        return out
    for ns in settings.cache_dir.iterdir():
        if not ns.is_dir():
            continue
        count = 0
        size = 0
        for p in ns.rglob("*.pkl"):
            count += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
        out[ns.name] = {"files": count, "bytes": size}
    return out
