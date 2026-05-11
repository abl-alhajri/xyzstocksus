"""Migration 005 — watchlist refresh: hard-delete 19 tickers + safety pre-check."""
from __future__ import annotations

import pytest


REMOVED = (
    "MARA", "DIS", "MCD", "NKE", "PEP", "PYPL", "COIN", "TMO", "PFE",
    "LLY", "COST", "CAT", "META", "MA", "AVGO", "MSFT", "UNH", "V", "HD",
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(db_dir))

    import importlib
    from config import settings as settings_module
    importlib.reload(settings_module)

    from db import connection
    importlib.reload(connection)
    connection.reset_init_state()

    from db import migrate
    importlib.reload(migrate)

    from db.repos import stocks, signals, sharia, positions, costs, runtime_config
    for m in (stocks, signals, sharia, positions, costs, runtime_config):
        importlib.reload(m)

    yield


def _reinsert_removed_with_children(conn):
    """Recreate the pre-refresh state: all 19 tickers seeded with child rows
    in every symbol-bearing table. FK off so child rows can land first."""
    conn.execute("PRAGMA foreign_keys=OFF")
    for sym in REMOVED:
        conn.execute(
            "INSERT INTO stocks_metadata "
            "(symbol, sector, btc_beta, agent_set, enabled, expected_status) "
            "VALUES (?, 'TECH_MEGA', 0.0, 'standard', 1, 'HALAL')",
            (sym,),
        )
        conn.execute(
            "INSERT INTO heuristic_scores (symbol, timestamp, score) "
            "VALUES (?, '2026-05-01', 50.0)",
            (sym,),
        )
        conn.execute(
            "INSERT INTO prescreen_results "
            "(symbol, timestamp, haiku_verdict, deep_analyze) "
            "VALUES (?, '2026-05-01', 0, 0)",
            (sym,),
        )
        conn.execute(
            "INSERT INTO signals (timestamp, symbol, decision, confidence) "
            "VALUES ('2026-05-01', ?, 'PASS', 0.4)",
            (sym,),
        )
        conn.execute(
            "INSERT INTO agent_outputs "
            "(symbol, timestamp, agent_name, round_num, output_json) "
            "VALUES (?, '2026-05-01', 'technical', 1, '{}')",
            (sym,),
        )
        conn.execute(
            "INSERT INTO financial_ratios_history "
            "(symbol, fetched_at, sharia_status) "
            "VALUES (?, '2026-05-01', 'HALAL')",
            (sym,),
        )
        conn.execute(
            "INSERT INTO compliance_alerts "
            "(symbol, alert_type, severity, sent_at) "
            "VALUES (?, 'STATUS_CHANGE', 'INFO', '2026-05-01')",
            (sym,),
        )
        conn.execute(
            "INSERT INTO user_positions "
            "(symbol, entry_date, entry_price, quantity, status) "
            "VALUES (?, '2026-05-01', 100.0, 5, 'CLOSED')",
            (sym,),
        )
        conn.execute(
            "INSERT INTO api_costs (timestamp, model, symbol, cost_usd) "
            "VALUES ('2026-05-01', 'haiku', ?, 0.001)",
            (sym,),
        )
    conn.execute("PRAGMA foreign_keys=ON")


def test_migration_005_purges_19_tickers():
    """005 must hard-delete the 19 removed tickers and all child rows."""
    from db.connection import get_conn
    from db.migrate import MIGRATIONS_DIR, run_migrations

    run_migrations()  # bootstrap (005 already applied, no-op for purge)
    with get_conn() as conn:
        _reinsert_removed_with_children(conn)

    sql = (MIGRATIONS_DIR / "005_watchlist_refresh.sql").read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)

        tables = (
            "stocks_metadata", "heuristic_scores", "prescreen_results",
            "signals", "agent_outputs", "financial_ratios_history",
            "compliance_alerts", "user_positions", "api_costs",
        )
        placeholders = ",".join("?" * len(REMOVED))
        for table in tables:
            n = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE symbol IN ({placeholders})",
                REMOVED,
            ).fetchone()["n"]
            assert n == 0, f"{table} still has rows for removed tickers"

        # Idempotent: second pass is a no-op (no error, no rows changed).
        before = conn.total_changes
        conn.executescript(sql)
        assert conn.total_changes == before


def test_migration_005_precheck_aborts_on_open_positions():
    """The Python pre-check must refuse the migration when any removed ticker
    still has an OPEN position. The user must close them first."""
    from db.connection import get_conn
    from db.migrate import MigrationAborted, _precheck_005, run_migrations

    run_migrations()
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO stocks_metadata "
            "(symbol, sector, btc_beta, agent_set, enabled, expected_status) "
            "VALUES ('META', 'TECH_MEGA', 0.4, 'standard', 1, 'MIXED')"
        )
        conn.execute(
            "INSERT INTO user_positions "
            "(symbol, entry_date, entry_price, quantity, status) "
            "VALUES ('META', '2026-05-01', 600.0, 10, 'OPEN')"
        )
        conn.execute("PRAGMA foreign_keys=ON")

        with pytest.raises(MigrationAborted) as exc_info:
            _precheck_005(conn)
        assert "META" in str(exc_info.value)
        assert "/sell" in str(exc_info.value)


def test_migration_005_precheck_passes_with_no_open_positions():
    """With no open positions on the 19 tickers, pre-check returns silently."""
    from db.connection import get_conn
    from db.migrate import _precheck_005, run_migrations

    run_migrations()
    with get_conn() as conn:
        # No-op: pre-check should not raise.
        _precheck_005(conn)


def test_migration_005_precheck_ignores_closed_positions():
    """CLOSED positions on removed tickers do NOT block the migration —
    they are historical records that the SQL is allowed to delete."""
    from db.connection import get_conn
    from db.migrate import _precheck_005, run_migrations

    run_migrations()
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO stocks_metadata "
            "(symbol, sector, btc_beta, agent_set, enabled, expected_status) "
            "VALUES ('PFE', 'HEALTHCARE_PHARMA', 0.0, 'standard', 1, 'HALAL')"
        )
        conn.execute(
            "INSERT INTO user_positions "
            "(symbol, entry_date, entry_price, quantity, status, "
            " closed_date, closed_price) "
            "VALUES ('PFE', '2026-01-01', 30.0, 100, 'CLOSED', "
            " '2026-04-01', 35.0)"
        )
        conn.execute("PRAGMA foreign_keys=ON")
        # Should not raise.
        _precheck_005(conn)


def test_watchlist_count_after_refresh_is_46():
    """Seeded watchlist size = 46 (43 stocks + 3 halal ETFs)."""
    from db.migrate import run_migrations
    from db.repos.stocks import list_all

    run_migrations()
    stocks = list_all(enabled_only=False)
    assert len(stocks) == 46


# Note: pure-config assertions (HARAM seeds, new-ticker categories) live in
# tests/test_config.py — they don't need the isolated_db fixture.
