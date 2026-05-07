"""Dashboard JSON + HTML routes.

Pages:
  /            — main dashboard (sectored grid + recent signals + macro ticker)
  /sharia      — Sharia tab (status counts, recent alerts, drift watchlist)

JSON API:
  /api/watchlist     — full watchlist + Sharia + heuristic
  /api/signals       — last N signals
  /api/cost          — today + month spend, per-agent breakdown
  /api/btc           — current BTC + regime
  /api/sharia        — weekly compliance JSON
  /api/macro         — latest quotes
  /api/stream        — SSE
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, render_template, request

from core import budget_guard, cost_tracker
from core.market_calendar import status as market_status
from dashboard import sse
from data.btc_feed import classify_regime, fetch_spot
from data.macro_feed import recent_quotes
from db.repos import sharia as sharia_repo
from db.repos import signals as signals_repo
from db.repos.stocks import latest_scores_all, list_all
from sharia.reporter import build_weekly_report

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/sharia")
def sharia_page():
    return render_template("sharia.html")


# --------------------------- JSON API ------------------------------------

@bp.get("/api/watchlist")
def api_watchlist():
    stocks = list_all(enabled_only=False)
    scores = latest_scores_all([s.symbol for s in stocks])
    by_sector = defaultdict(list)
    for s in stocks:
        score_row = scores.get(s.symbol, {})
        by_sector[s.sector].append({
            "symbol": s.symbol,
            "btc_beta": s.btc_beta,
            "agent_set": s.agent_set,
            "enabled": s.enabled,
            "sharia_status": s.sharia_status,
            "sharia_status_verified_at": s.sharia_status_verified_at,
            "score": score_row.get("score"),
            "rsi": score_row.get("rsi"),
        })
    return jsonify({
        "sectors": [{"sector": k, "stocks": v} for k, v in sorted(by_sector.items())],
        "total": len(stocks),
        "ts": datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/signals")
def api_signals():
    limit = max(1, min(int(request.args.get("limit", 30)), 200))
    rows = signals_repo.recent(limit)
    return jsonify({"signals": rows, "ts": datetime.now(timezone.utc).isoformat()})


@bp.get("/api/cost")
def api_cost():
    bs = budget_guard.state()
    return jsonify({
        "today_usd": bs.today_usd,
        "month_usd": bs.month_usd,
        "deep_count_today": bs.deep_count_today,
        "quick_only": bs.quick_only,
        "daily_soft_breached": bs.daily_soft_breached,
        "daily_hard_breached": bs.daily_hard_breached,
        "monthly_warn_breached": bs.monthly_warn_breached,
        "monthly_hard_breached": bs.monthly_hard_breached,
        "deep_cap_reached": bs.deep_cap_reached,
        "per_agent_today": cost_tracker.per_agent_today(),
    })


@bp.get("/api/btc")
def api_btc():
    snap = fetch_spot(use_cache=True)
    regime = classify_regime()
    return jsonify({
        "price": snap.price if snap else None,
        "regime": regime.label,
        "sma_20": regime.sma_20,
        "sma_50": regime.sma_50,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/sharia")
def api_sharia():
    rep = build_weekly_report(days=7)
    return jsonify({
        "generated_at": rep.generated_at,
        "counts": rep.counts,
        "tier_changes": rep.tier_changes,
        "drift_warnings": rep.drift_warnings,
        "halal": rep.halal,
        "mixed": rep.mixed,
        "haram": rep.haram,
        "pending": rep.pending,
        "recent_alerts": sharia_repo.recent_alerts(limit=30),
    })


@bp.get("/api/macro")
def api_macro():
    quotes = recent_quotes(limit=15)
    return jsonify({"quotes": quotes})


@bp.get("/api/market")
def api_market():
    s = market_status()
    return jsonify({
        "label": s.label,
        "is_open": s.is_open,
        "is_early_close": s.is_early_close,
        "note": s.note,
    })


@bp.get("/api/stream")
def api_stream():
    q = sse.subscribe()

    def gen():
        try:
            yield from sse.stream(q)
        finally:
            sse.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                   headers={"Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no"})
