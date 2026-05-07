"""Commit 1 smoke test — verify /health and / respond OK."""
from __future__ import annotations

from workers.web import app


def test_health_returns_ok():
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["service"] == "xyzstocksus"
    assert "uptime_s" in body
    assert "now_utc" in body


def test_root_redirects_to_health_hint():
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["see"] == "/health"
