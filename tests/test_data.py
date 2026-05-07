"""Commit 4 tests — cache, macro sentiment, insider cluster detection.

These tests cover the pure-logic and persistence pieces of the data layer.
Network-dependent fetchers (yfinance, Coinbase, Fed RSS, SEC EDGAR) are not
exercised here — they're verified manually on Railway with real credentials.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from config import settings as smod
    importlib.reload(smod)
    from db import connection as conn_mod
    importlib.reload(conn_mod)
    conn_mod.reset_init_state()
    from db import migrate
    importlib.reload(migrate)
    migrate.run_migrations()
    yield


# ----------------------------- cache -------------------------------------

def test_cache_roundtrip():
    from core import cache
    assert cache.get("test", "k", 60) is None
    cache.set_("test", "k", {"v": 1})
    got = cache.get("test", "k", 60)
    assert got == {"v": 1}


def test_cache_expires():
    from core import cache
    cache.set_("test", "expire", "value")
    # ttl=0 → always expired
    assert cache.get("test", "expire", 0) is None


def test_cache_invalidate():
    from core import cache
    cache.set_("test", "del", 42)
    cache.invalidate("test", "del")
    assert cache.get("test", "del", 60) is None


def test_cache_get_or_compute_calls_compute_once():
    from core import cache
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"a": 1}

    a = cache.get_or_compute("test", "compute", 60, compute)
    b = cache.get_or_compute("test", "compute", 60, compute)
    assert a == b == {"a": 1}
    assert calls["n"] == 1


# ------------------------ macro sentiment classifier ----------------------

def test_sentiment_hawkish():
    from data.macro_feed import classify_sentiment
    text = "The Committee judges that holding rates restrictive is appropriate to keep inflation persistent risks contained"
    assert classify_sentiment(text) == "HAWKISH"


def test_sentiment_dovish():
    from data.macro_feed import classify_sentiment
    text = "Inflation is moderating toward target; a cut may be appropriate to support a soft landing"
    assert classify_sentiment(text) == "DOVISH"


def test_sentiment_neutral():
    from data.macro_feed import classify_sentiment
    assert classify_sentiment("The Committee will continue to monitor incoming data.") == "NEUTRAL"


# ------------------------ insider cluster detection ----------------------

def _trade(symbol, insider, transaction="P - Purchase", title="", days_ago=1):
    from data.openinsider import InsiderTrade
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return InsiderTrade(
        symbol=symbol,
        insider=insider,
        title=title,
        transaction=transaction,
        trade_date=ts,
        qty=None, price=None, value=None, url=None,
    )


def test_cluster_qualifies_with_three_buyers_and_officer():
    from data.openinsider import detect_clusters
    trades = [
        _trade("TSLA", "Alice Adams", title="CFO"),
        _trade("TSLA", "Bob Brown"),
        _trade("TSLA", "Carol Chen"),
    ]
    clusters = detect_clusters(trades)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.symbol == "TSLA"
    assert c.buyer_count == 3
    assert c.has_officer is True
    assert c.qualifies is True


def test_cluster_skips_when_no_officer():
    from data.openinsider import detect_clusters
    trades = [
        _trade("TSLA", "Alice", title=""),
        _trade("TSLA", "Bob",   title=""),
        _trade("TSLA", "Carol", title=""),
    ]
    clusters = detect_clusters(trades)
    assert len(clusters) == 1
    assert clusters[0].qualifies is False


def test_cluster_ignores_sales():
    from data.openinsider import detect_clusters
    trades = [
        _trade("NVDA", "Alice", transaction="S - Sale", title="CEO"),
        _trade("NVDA", "Bob",   transaction="S - Sale"),
        _trade("NVDA", "Carol", transaction="S - Sale"),
    ]
    clusters = detect_clusters(trades)
    assert clusters == []


def test_cluster_ignores_old_trades():
    from data.openinsider import detect_clusters
    trades = [
        _trade("MSTR", "Alice", title="CEO", days_ago=30),
        _trade("MSTR", "Bob",   days_ago=20),
        _trade("MSTR", "Carol", days_ago=15),
    ]
    clusters = detect_clusters(trades, days=14)
    assert clusters == []


def test_cluster_below_min_buyers():
    from data.openinsider import detect_clusters
    trades = [
        _trade("AMD", "Alice", title="CEO"),
        _trade("AMD", "Bob"),
    ]
    clusters = detect_clusters(trades, min_buyers=3)
    assert clusters == []


# ------------------------ btc dump detector ------------------------------

def test_is_dump_triggers_when_5pct_drop_in_window():
    from db.connection import get_conn
    from data.btc_feed import is_dump

    now = datetime.now(timezone.utc)
    # Drop from 100k → 94k inside the 60min window = 6% drop
    rows = [
        (now - timedelta(minutes=55), 100_000.0),
        (now - timedelta(minutes=30), 97_000.0),
        (now - timedelta(minutes=5),   94_000.0),
    ]
    with get_conn() as conn:
        for ts, price in rows:
            conn.execute(
                "INSERT INTO btc_snapshots (timestamp, price, regime, source) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), price, "NEUTRAL", "test"),
            )
    assert is_dump(drop_pct=0.05, window_min=60) is True


def test_is_dump_silent_when_stable():
    from db.connection import get_conn
    from data.btc_feed import is_dump

    now = datetime.now(timezone.utc)
    rows = [
        (now - timedelta(minutes=50), 100_000.0),
        (now - timedelta(minutes=25), 100_500.0),
        (now - timedelta(minutes=2),  100_200.0),
    ]
    with get_conn() as conn:
        for ts, price in rows:
            conn.execute(
                "INSERT INTO btc_snapshots (timestamp, price, regime, source) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), price, "BULL", "test"),
            )
    assert is_dump(drop_pct=0.05, window_min=60) is False
