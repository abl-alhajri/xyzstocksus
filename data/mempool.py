"""mempool.space metrics for the BTC Macro agent (only for btc_full names)."""
from __future__ import annotations

from dataclasses import dataclass

from core import cache
from core.logger import get_logger

log = get_logger("data.mempool")

DIFFICULTY_URL = "https://mempool.space/api/v1/difficulty-adjustment"
HASHRATE_URL = "https://mempool.space/api/v1/mining/hashrate/3d"
MEMPOOL_URL = "https://mempool.space/api/mempool"


@dataclass
class BTCNetworkStats:
    hashrate_eh_s: float | None
    difficulty_change_pct: float | None
    mempool_count: int | None
    mempool_vsize: int | None
    fee_low: float | None
    fee_med: float | None
    fee_high: float | None


def fetch_network_stats(*, use_cache: bool = True) -> BTCNetworkStats | None:
    if use_cache:
        cached = cache.get("mempool", "stats", 30 * 60)  # 30 min
        if cached is not None:
            return cached

    out = BTCNetworkStats(None, None, None, None, None, None, None)

    try:
        import requests  # type: ignore
    except ImportError:
        log.warning("requests not installed — mempool stats unavailable")
        return out

    try:
        r = requests.get(DIFFICULTY_URL, timeout=8)
        r.raise_for_status()
        data = r.json()
        out.difficulty_change_pct = float(data.get("difficultyChange", 0))
    except Exception as exc:
        log.warning("mempool difficulty failed", extra={"err": str(exc)})

    try:
        r = requests.get(HASHRATE_URL, timeout=8)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            out.hashrate_eh_s = float(data.get("currentHashrate", 0)) / 1e18
        elif isinstance(data, list) and data:
            last = data[-1]
            out.hashrate_eh_s = float(last.get("avgHashrate", 0)) / 1e18
    except Exception as exc:
        log.warning("mempool hashrate failed", extra={"err": str(exc)})

    try:
        r = requests.get(MEMPOOL_URL, timeout=8)
        r.raise_for_status()
        data = r.json()
        out.mempool_count = int(data.get("count", 0))
        out.mempool_vsize = int(data.get("vsize", 0))
    except Exception as exc:
        log.warning("mempool size failed", extra={"err": str(exc)})

    try:
        r = requests.get("https://mempool.space/api/v1/fees/recommended", timeout=8)
        r.raise_for_status()
        data = r.json()
        out.fee_low = float(data.get("hourFee", 0))
        out.fee_med = float(data.get("halfHourFee", 0))
        out.fee_high = float(data.get("fastestFee", 0))
    except Exception as exc:
        log.warning("mempool fees failed", extra={"err": str(exc)})

    if use_cache:
        cache.set_("mempool", "stats", out)
    return out
