"""Commit 15 tests — dashboard JSON endpoints and SSE."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from config import settings as smod
    importlib.reload(smod)
    from db import connection
    importlib.reload(connection)
    connection.reset_init_state()
    from db import migrate
    importlib.reload(migrate)
    migrate.run_migrations()
    yield


def _client():
    from dashboard.app import create_app
    app = create_app()
    return app.test_client()


def test_health_lives_on_dashboard():
    c = _client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_index_renders():
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "XYZStocksUS" in body
    assert "Watchlist" in body


def test_sharia_page_renders():
    c = _client()
    r = c.get("/sharia")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Sharia" in body
    # Arabic labels
    assert "شرعي" in body
    assert "مختلط" in body
    assert "غير شرعي" in body


def test_api_watchlist_shape():
    c = _client()
    r = c.get("/api/watchlist")
    assert r.status_code == 200
    body = r.get_json()
    assert "sectors" in body
    # Post-migration 005: 43 stocks + 3 halal ETFs = 46 tickers
    assert body["total"] >= 46
    # Every sector entry has a list of stocks
    for sec in body["sectors"]:
        assert "sector" in sec
        assert isinstance(sec["stocks"], list)


def test_api_signals_empty_initially():
    c = _client()
    r = c.get("/api/signals?limit=5")
    assert r.status_code == 200
    body = r.get_json()
    assert "signals" in body


def test_api_cost_present():
    c = _client()
    r = c.get("/api/cost")
    body = r.get_json()
    assert "today_usd" in body
    assert "month_usd" in body
    assert "deep_count_today" in body
    assert "per_agent_today" in body


def test_api_market_returns_label():
    c = _client()
    r = c.get("/api/market")
    body = r.get_json()
    assert "label" in body
    assert "is_open" in body


def test_api_sharia_groups_status_buckets():
    c = _client()
    r = c.get("/api/sharia")
    body = r.get_json()
    assert "halal" in body
    assert "mixed" in body
    assert "haram" in body
    assert "pending" in body
    assert isinstance(body["counts"], dict)


def test_sse_publish_subscribe():
    """Round-trip an SSE message through the in-process broadcaster."""
    from dashboard import sse
    q = sse.subscribe()
    sse.publish("signal", {"symbol": "TSLA", "decision": "BUY"})
    msg = q.get_nowait()
    assert "event: signal" in msg
    assert "TSLA" in msg
    sse.unsubscribe(q)
