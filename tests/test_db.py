"""Commit 3 tests — schema, migrations, repos."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the DB at a temp file so each test starts clean."""
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(db_dir))

    # Force re-import of settings so its DATA_DIR picks up the env change
    import importlib
    from config import settings as settings_module
    importlib.reload(settings_module)

    from db import connection
    importlib.reload(connection)
    connection.reset_init_state()

    # Reload migrate + repos that captured `get_conn` at import time
    from db import migrate
    importlib.reload(migrate)

    from db.repos import stocks, signals, sharia, positions, costs, runtime_config
    for m in (stocks, signals, sharia, positions, costs, runtime_config):
        importlib.reload(m)

    yield


def test_migrations_run_idempotently():
    from db.migrate import run_migrations
    from config.watchlist import WATCHLIST

    first = run_migrations()
    assert "001_initial" in first["applied"]
    assert first["seeded_symbols"] == len(WATCHLIST)

    # Second run should apply nothing new and seed nothing new
    second = run_migrations()
    assert second["applied"] == []
    assert "001_initial" in second["already_present"]
    assert second["seeded_symbols"] == 0


def test_migration_003_renames_sq_across_all_symbol_tables():
    """003 must rename SQ in every symbol-bearing table and be idempotent."""
    from db.connection import get_conn
    from db.migrate import MIGRATIONS_DIR, run_migrations

    run_migrations()  # bootstrap schema; XYZ is now seeded (no SQ row)

    # Simulate the production pre-rename state: SQ exists, XYZ does not.
    # FK off so we can swap the parent row without disturbing children.
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM stocks_metadata WHERE symbol='XYZ'")
        conn.execute(
            "INSERT INTO stocks_metadata "
            "(symbol, sector, btc_beta, agent_set, enabled, expected_status) "
            "VALUES ('SQ', 'CRYPTO_ADJACENT', 1.2, 'standard', 1, 'MIXED')"
        )
        conn.execute(
            "INSERT INTO financial_ratios_history (symbol, fetched_at, sharia_status) "
            "VALUES ('SQ', '2025-01-01', 'MIXED')"
        )
        conn.execute(
            "INSERT INTO signals (timestamp, symbol, decision, confidence) "
            "VALUES ('2025-01-01', 'SQ', 'HOLD', 0.5)"
        )
        conn.execute(
            "INSERT INTO compliance_alerts (symbol, alert_type, severity, sent_at) "
            "VALUES ('SQ', 'STATUS_CHANGE', 'INFO', '2025-01-01')"
        )
        conn.execute("PRAGMA foreign_keys=ON")

    # Apply 003 directly; bootstrap already recorded it in schema_migrations.
    sql = (MIGRATIONS_DIR / "003_rename_sq_to_xyz.sql").read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)

        for table in ("stocks_metadata", "financial_ratios_history",
                      "signals", "compliance_alerts"):
            sq_row = conn.execute(
                f"SELECT 1 FROM {table} WHERE symbol='SQ'"
            ).fetchone()
            xyz_row = conn.execute(
                f"SELECT 1 FROM {table} WHERE symbol='XYZ'"
            ).fetchone()
            assert sq_row is None, f"{table} still has SQ after rename"
            assert xyz_row is not None, f"{table} missing XYZ after rename"

        # Idempotent: a second pass finds nothing to do.
        conn.executescript(sql)
        assert conn.execute(
            "SELECT 1 FROM stocks_metadata WHERE symbol='SQ'"
        ).fetchone() is None


def test_migration_004_purges_disabled_btc_miners():
    """004 must hard-delete CLSK/WULF/CIFR/HUT/BTBT and all child rows, idempotently."""
    from db.connection import get_conn
    from db.migrate import MIGRATIONS_DIR, run_migrations

    run_migrations()  # bootstrap

    miners = ("CLSK", "WULF", "CIFR", "HUT", "BTBT")

    # Simulate the production pre-purge state: re-insert the 5 miners with
    # rows in every symbol-bearing table. FK off so child rows can land
    # before re-creating their parent.
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for sym in miners:
            conn.execute(
                "INSERT INTO stocks_metadata "
                "(symbol, sector, btc_beta, agent_set, enabled, expected_status) "
                "VALUES (?, 'BTC_MINER', 2.5, 'btc_full', 0, 'HALAL')",
                (sym,),
            )
            conn.execute(
                "INSERT INTO heuristic_scores (symbol, timestamp, score) "
                "VALUES (?, '2026-04-01', 50.0)",
                (sym,),
            )
            conn.execute(
                "INSERT INTO prescreen_results "
                "(symbol, timestamp, haiku_verdict, deep_analyze) "
                "VALUES (?, '2026-04-01', 0, 0)",
                (sym,),
            )
            conn.execute(
                "INSERT INTO signals (timestamp, symbol, decision, confidence) "
                "VALUES ('2026-04-01', ?, 'PASS', 0.4)",
                (sym,),
            )
            conn.execute(
                "INSERT INTO agent_outputs "
                "(symbol, timestamp, agent_name, round_num, output_json) "
                "VALUES (?, '2026-04-01', 'technical', 1, '{}')",
                (sym,),
            )
            conn.execute(
                "INSERT INTO financial_ratios_history "
                "(symbol, fetched_at, sharia_status) "
                "VALUES (?, '2026-04-01', 'HALAL')",
                (sym,),
            )
            conn.execute(
                "INSERT INTO compliance_alerts "
                "(symbol, alert_type, severity, sent_at) "
                "VALUES (?, 'STATUS_CHANGE', 'INFO', '2026-04-01')",
                (sym,),
            )
            conn.execute(
                "INSERT INTO api_costs "
                "(timestamp, model, symbol, cost_usd) "
                "VALUES ('2026-04-01', 'haiku', ?, 0.001)",
                (sym,),
            )
        conn.execute("PRAGMA foreign_keys=ON")

    # Apply 004 directly; bootstrap already recorded it in schema_migrations.
    sql = (MIGRATIONS_DIR / "004_purge_disabled_btc_miners.sql").read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)

        for table in ("stocks_metadata", "heuristic_scores", "prescreen_results",
                      "signals", "agent_outputs", "financial_ratios_history",
                      "compliance_alerts", "api_costs"):
            placeholders = ",".join("?" * len(miners))
            remaining = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE symbol IN ({placeholders})",
                miners,
            ).fetchone()
            assert remaining["n"] == 0, f"{table} still has rows for purged miners"

        # Idempotent: a second pass finds nothing to do, no error.
        conn.executescript(sql)
        sm_count = conn.execute(
            f"SELECT COUNT(*) AS n FROM stocks_metadata "
            f"WHERE symbol IN ({','.join('?' * len(miners))})",
            miners,
        ).fetchone()
        assert sm_count["n"] == 0


def test_all_tables_exist():
    from db.migrate import run_migrations
    from db.connection import get_conn

    run_migrations()
    expected = {
        "schema_migrations",
        "stocks_metadata",
        "heuristic_scores",
        "prescreen_results",
        "signals",
        "agent_outputs",
        "btc_snapshots",
        "macro_quotes",
        "macro_events",
        "api_costs",
        "runtime_config",
        "command_log",
        "financial_ratios_history",
        "compliance_alerts",
        "user_positions",
    }
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    found = {r["name"] for r in rows}
    missing = expected - found
    assert not missing, f"missing tables: {missing}"


def test_stocks_seeded_with_correct_agent_set():
    from db.migrate import run_migrations
    from db.repos.stocks import get

    run_migrations()
    # MSTR is BTC_TREASURY → btc_full
    assert get("MSTR").agent_set == "btc_full"
    assert get("MSTR").sharia_status == "PENDING"
    # AAPL → standard
    assert get("AAPL").agent_set == "standard"
    # HLAL → lean
    assert get("HLAL").agent_set == "lean"


def test_set_enabled_and_sharia_status():
    from db.migrate import run_migrations
    from db.repos import stocks

    run_migrations()
    stocks.set_enabled("AAPL", False)
    assert not stocks.get("AAPL").enabled

    stocks.set_sharia_status("AAPL", "HALAL")
    assert stocks.get("AAPL").sharia_status == "HALAL"


def test_signals_insert_and_recent():
    from db.migrate import run_migrations
    from db.repos import signals

    run_migrations()
    sid = signals.insert_signal(
        symbol="TSLA",
        decision="BUY",
        trade_type="SWING",
        confidence=0.78,
        sharia_status="HALAL",
        full_synthesis={"summary": "ok"},
    )
    assert sid > 0

    rec = signals.recent(10)
    assert len(rec) == 1
    assert rec[0]["symbol"] == "TSLA"
    assert rec[0]["confidence"] == 0.78


def test_dedup_window():
    from db.migrate import run_migrations
    from db.repos import signals

    run_migrations()
    sid = signals.insert_signal(
        symbol="NVDA",
        decision="BUY",
        trade_type="SWING",
        confidence=0.70,
        sharia_status="HALAL",
        full_synthesis=None,
    )
    signals.mark_sent(sid, telegram_msg_id=1234)

    # Same confidence → suppress (dedup)
    assert signals.should_dedup("NVDA", new_confidence=0.70, window_hours=4, confidence_jump=0.10)
    # Big jump → bypass dedup
    assert not signals.should_dedup("NVDA", new_confidence=0.85, window_hours=4, confidence_jump=0.10)
    # Different symbol with no history → no dedup
    assert not signals.should_dedup("AMD", new_confidence=0.70, window_hours=4, confidence_jump=0.10)


def test_agent_outputs_for_signal():
    from db.migrate import run_migrations
    from db.repos import signals

    run_migrations()
    sid = signals.insert_signal(
        symbol="MSTR",
        decision="BUY",
        trade_type="SWING",
        confidence=0.72,
        sharia_status="MIXED",
        full_synthesis=None,
    )
    signals.insert_agent_output(
        signal_id=sid, symbol="MSTR", agent_name="technical", round_num=1,
        output={"trend": "up"}, confidence=0.7, decision="BUY",
        input_tokens=2000, output_tokens=400, cached_tokens=1500,
        cost_usd=0.012, latency_ms=1234,
    )
    signals.insert_agent_output(
        signal_id=sid, symbol="MSTR", agent_name="sharia", round_num=1,
        output={"status": "MIXED"}, confidence=0.6, decision="HOLD",
        input_tokens=1800, output_tokens=300, cached_tokens=1500,
        cost_usd=0.010, latency_ms=900,
    )
    outs = signals.outputs_for_signal(sid)
    assert len(outs) == 2
    names = {o["agent_name"] for o in outs}
    assert names == {"technical", "sharia"}


def test_sharia_ratios_and_history():
    from db.migrate import run_migrations
    from db.repos import sharia

    run_migrations()
    for q, debt in enumerate([0.18, 0.22, 0.26, 0.28]):
        sharia.insert_ratios(
            symbol="TSLA",
            market_cap=1_000_000_000,
            total_debt=debt * 1_000_000_000,
            interest_bearing_debt=debt * 1_000_000_000,
            cash_and_securities=0.1 * 1_000_000_000,
            total_revenue=200_000_000,
            impermissible_revenue=2_000_000,
            debt_ratio=debt,
            cash_ratio=0.1,
            impermissible_ratio=0.01,
            sharia_status="MIXED",
            risk_tier="YELLOW",
            filing_date=f"2025-{(q+1)*3:02d}-30",
            filing_type="10-Q",
        )
    hist = sharia.quarterly_history("TSLA", limit=4)
    assert len(hist) == 4
    assert hist[0]["debt_ratio"] == 0.18
    assert hist[-1]["debt_ratio"] == 0.28

    latest = sharia.latest_ratios("TSLA")
    assert latest is not None


def test_compliance_alerts():
    from db.migrate import run_migrations
    from db.repos import sharia

    run_migrations()
    sharia.insert_alert(
        symbol="MSTR",
        alert_type="DRIFT_WARN",
        old_value="0.25",
        new_value="0.28",
        severity="WARN",
    )
    rec = sharia.recent_alerts()
    assert len(rec) == 1
    assert rec[0]["alert_type"] == "DRIFT_WARN"


def test_positions_lifecycle():
    from db.migrate import run_migrations
    from db.repos import positions

    run_migrations()
    pid = positions.open_position(
        symbol="TSLA",
        entry_price=245.0,
        quantity=10,
        sharia_status_at_entry="HALAL",
        notes="initial",
    )
    assert positions.list_open()[0]["id"] == pid
    assert "TSLA" in positions.open_symbols()

    closed = positions.close_all_for_symbol("TSLA", closed_price=260.0)
    assert closed == 1
    assert positions.open_symbols() == []


def test_costs_aggregations():
    from db.migrate import run_migrations
    from db.repos import costs

    run_migrations()
    costs.insert_cost(
        model="claude-haiku-4-5", agent="prescreen", symbol="TSLA",
        input_tokens=600, output_tokens=120, cached_tokens=0,
        cache_creation_tokens=0, cost_usd=0.0012,
    )
    costs.insert_cost(
        model="claude-sonnet-4-6", agent="technical", symbol="TSLA",
        input_tokens=2200, output_tokens=550, cached_tokens=1500,
        cache_creation_tokens=0, cost_usd=0.0149,
    )
    costs.insert_cost(
        model="claude-sonnet-4-6", agent="sharia", symbol="TSLA",
        input_tokens=2000, output_tokens=400, cached_tokens=1500,
        cache_creation_tokens=0, cost_usd=0.011,
    )
    today = costs.total_today()
    assert today == pytest.approx(0.0012 + 0.0149 + 0.011, rel=1e-3)
    assert costs.deep_analyses_today() == 2

    breakdown = costs.per_agent_today()
    assert "technical" in breakdown
    assert "sharia" in breakdown


def test_runtime_config_set_get():
    from db.migrate import run_migrations
    from db.repos import runtime_config

    run_migrations()
    runtime_config.set_value("alerts_paused", True)
    assert runtime_config.get_value("alerts_paused") is True

    runtime_config.set_value("min_confidence", 0.7)
    assert runtime_config.get_value("min_confidence") == 0.7

    assert runtime_config.get_value("missing", default="x") == "x"


def test_command_log_writes():
    from db.migrate import run_migrations
    from db.repos import runtime_config
    from db.connection import get_conn

    run_migrations()
    runtime_config.log_command(
        chat_id="8588842240",
        command="/analyze",
        args="TSLA",
        success=True,
    )
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM command_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["command"] == "/analyze"
    assert rows[0]["success"] == 1
