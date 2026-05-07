"""BTC live price + regime classifier (Coinbase + yfinance daily)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import cache
from core.logger import get_logger
from db.connection import get_conn

log = get_logger("data.btc")

COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


@dataclass
class BTCSnapshot:
    price: float
    timestamp: str
    source: str


@dataclass
class BTCRegime:
    label: str             # BULL | BEAR | NEUTRAL
    sma_20: float | None
    sma_50: float | None
    last_close: float | None
    notes: str = ""


def fetch_spot(*, use_cache: bool = True, timeout: float = 5.0) -> BTCSnapshot | None:
    """Live spot from Coinbase. 60s cache to avoid hammering on every scan."""
    if use_cache:
        cached = cache.get("btc_spot", "BTC-USD", cache.TTL_BTC_INTRA)
        if cached is not None:
            return cached
    try:
        import requests  # type: ignore
        r = requests.get(COINBASE_SPOT_URL, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        price = float(data["data"]["amount"])
    except Exception as exc:
        log.warning("coinbase spot failed", extra={"err": str(exc)})
        return None
    snap = BTCSnapshot(price=price, timestamp=_now(), source="coinbase")
    if use_cache:
        cache.set_("btc_spot", "BTC-USD", snap)
    _persist_snapshot(snap, regime_label=None)
    return snap


def classify_regime() -> BTCRegime:
    """Compute SMA(20)/SMA(50) on daily BTC closes via yfinance and label.

    BULL  : last close > SMA20 > SMA50
    BEAR  : last close < SMA20 < SMA50
    NEUTRAL: otherwise

    Falls back gracefully if data is missing.
    """
    try:
        from data.prices import fetch_history
        res = fetch_history(["BTC-USD"], period="120d", interval="1d", use_cache=True)
        df = res.frames.get("BTC-USD")
        if df is None or len(df) < 50:
            return BTCRegime("NEUTRAL", None, None, None, notes="insufficient history")
        closes = df["Close"].astype(float)
        sma20 = float(closes.rolling(20).mean().iloc[-1])
        sma50 = float(closes.rolling(50).mean().iloc[-1])
        last = float(closes.iloc[-1])
        if last > sma20 > sma50:
            label = "BULL"
        elif last < sma20 < sma50:
            label = "BEAR"
        else:
            label = "NEUTRAL"
        return BTCRegime(label=label, sma_20=sma20, sma_50=sma50,
                         last_close=last, notes="sma20/sma50 trend filter")
    except Exception as exc:
        log.warning("regime calc failed", extra={"err": str(exc)})
        return BTCRegime("NEUTRAL", None, None, None, notes=f"error: {exc}")


def is_dump(*, drop_pct: float, window_min: int) -> bool:
    """Return True if BTC has dropped > drop_pct over the last window_min minutes.

    Uses persisted btc_snapshots rows. The scheduler / a 1-min ping job feeds
    this table; the function is read-only.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, price FROM btc_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff,),
        ).fetchall()
    if len(rows) < 2:
        return False
    high = max(r["price"] for r in rows)
    last = rows[-1]["price"]
    if high <= 0:
        return False
    drop = (high - last) / high
    return drop >= drop_pct


def _persist_snapshot(snap: BTCSnapshot, regime_label: Optional[str]) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO btc_snapshots (timestamp, price, regime, source)
                VALUES (?, ?, ?, ?)
                """,
                (snap.timestamp, snap.price, regime_label, snap.source),
            )
    except Exception as exc:  # pragma: no cover
        log.warning("btc snapshot persist failed", extra={"err": str(exc)})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
