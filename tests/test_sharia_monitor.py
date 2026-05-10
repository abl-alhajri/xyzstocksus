"""Commit 7 tests — sharia monitor (daily/weekly) + drift detection + reporter."""
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


def _good_yf(symbol: str) -> dict:
    """Yields a HALAL-shape input."""
    return {
        "marketCap": 100e9,
        "totalDebt": 5e9,           # 5%
        "totalCash": 8e9,           # 8%
        "totalRevenue": 80e9,
    }


def _bad_yf(symbol: str) -> dict:
    """Yields a HARAM-shape input — debt 40%."""
    return {
        "marketCap": 100e9,
        "totalDebt": 40e9,
        "totalCash": 5e9,
        "totalRevenue": 50e9,
    }


def _no_facts(symbol: str) -> None:
    return None


def _market_cap(symbol: str) -> float:
    return 100e9


def _filing_today(symbol: str) -> str:
    from datetime import date
    return date.today().isoformat()


def _filing_old(symbol: str) -> str:
    return "2024-01-15"


def test_daily_check_runs_on_open_positions_only():
    from db.repos import positions
    from sharia.monitor import run_daily_check

    positions.open_position(
        symbol="TSLA", entry_price=200.0, quantity=10,
        sharia_status_at_entry="HALAL",
    )
    report = run_daily_check(
        fetch_yfinance_info=_good_yf,
        fetch_company_facts=_no_facts,
        fetch_market_cap=_market_cap,
        fetch_latest_filing_date=_filing_today,
    )
    assert report.checked == ["TSLA"]
    assert "TSLA" in report.re_verified


def test_daily_check_skips_when_filing_unchanged():
    from db.repos import positions, sharia as sharia_repo
    from sharia.monitor import run_daily_check

    positions.open_position(
        symbol="TSLA", entry_price=200.0, quantity=10,
        sharia_status_at_entry="HALAL",
    )
    # Pre-seed prior verification at the same filing_date
    sharia_repo.insert_ratios(
        symbol="TSLA", market_cap=100e9, total_debt=5e9,
        interest_bearing_debt=5e9, cash_and_securities=8e9,
        total_revenue=80e9, impermissible_revenue=0.0,
        debt_ratio=0.05, cash_ratio=0.08, impermissible_ratio=0.0,
        sharia_status="HALAL", risk_tier="GREEN",
        filing_date="2025-09-30", filing_type="10-Q",
    )
    report = run_daily_check(
        fetch_yfinance_info=_good_yf,
        fetch_company_facts=_no_facts,
        fetch_market_cap=_market_cap,
        fetch_latest_filing_date=lambda s: "2025-09-30",
    )
    assert report.checked == ["TSLA"]
    assert "TSLA" not in report.re_verified  # skipped — same filing


def test_weekly_full_scan_covers_watchlist():
    from sharia.monitor import run_weekly_full_scan

    report = run_weekly_full_scan(
        symbols=["AAPL", "TSLA"],
        fetch_yfinance_info=_good_yf,
        fetch_company_facts=_no_facts,
        fetch_market_cap=_market_cap,
    )
    assert set(report.checked) == {"AAPL", "TSLA"}
    assert set(report.re_verified) == {"AAPL", "TSLA"}


def test_weekly_emits_status_change_alert():
    from db.repos import sharia as sharia_repo
    from sharia.monitor import run_weekly_full_scan

    # Pre-seed AAPL as HALAL
    sharia_repo.insert_ratios(
        symbol="AAPL", market_cap=3e12, total_debt=1e11,
        interest_bearing_debt=1e11, cash_and_securities=6e10,
        total_revenue=3.8e11, impermissible_revenue=0.0,
        debt_ratio=0.033, cash_ratio=0.020, impermissible_ratio=0.0,
        sharia_status="HALAL", risk_tier="GREEN",
        filing_date="2025-09-30", filing_type="10-Q",
    )
    # Now run weekly with HARAM-shape data
    report = run_weekly_full_scan(
        symbols=["AAPL"],
        fetch_yfinance_info=_bad_yf,
        fetch_company_facts=_no_facts,
        fetch_market_cap=_market_cap,
    )
    assert any(c["symbol"] == "AAPL" and c["new_status"] == "HARAM"
               for c in report.tier_changes)

    alerts = sharia_repo.alerts_for_symbol("AAPL")
    types = {a["alert_type"] for a in alerts}
    assert "STATUS_CHANGE" in types


def test_drift_warning_alert_fires_once_per_filing():
    from db.repos import sharia as sharia_repo
    from sharia.monitor import run_weekly_full_scan

    # Pre-seed 4 rising-debt quarters
    for q, debt in enumerate([0.20, 0.23, 0.26, 0.28]):
        sharia_repo.insert_ratios(
            symbol="TSLA", market_cap=1e9, total_debt=debt*1e9,
            interest_bearing_debt=debt*1e9, cash_and_securities=0.05*1e9,
            total_revenue=2e8, impermissible_revenue=2e6,
            debt_ratio=debt, cash_ratio=0.05, impermissible_ratio=0.01,
            sharia_status="MIXED", risk_tier="YELLOW",
            filing_date=f"2025-{(q+1)*3:02d}-30",
            filing_type="10-Q",
        )

    def _drift_yf(_sym):
        return {
            "marketCap": 1e9,
            "totalDebt": 0.28e9,
            "totalCash": 0.05e9,
            "totalRevenue": 2e8,
        }

    # First run → drift fires
    r1 = run_weekly_full_scan(
        symbols=["TSLA"],
        fetch_yfinance_info=_drift_yf,
        fetch_company_facts=_no_facts,
        fetch_market_cap=lambda s: 1e9,
    )
    assert any(d["symbol"] == "TSLA" for d in r1.drift_warnings)

    drift_alerts = [a for a in sharia_repo.alerts_for_symbol("TSLA")
                    if a["alert_type"] == "DRIFT_WARN"]
    assert len(drift_alerts) == 1


def test_weekly_reporter_renders():
    from db.repos import sharia as sharia_repo, stocks as stocks_repo
    from sharia.reporter import build_weekly_report, render_html

    stocks_repo.set_sharia_status("AAPL", "HALAL")
    stocks_repo.set_sharia_status("TSLA", "MIXED")
    sharia_repo.insert_alert(
        symbol="MSTR", alert_type="DRIFT_WARN",
        old_value=None, new_value="2025-09-30", severity="WARN",
    )
    rep = build_weekly_report(days=7)
    assert rep.counts.get("HALAL", 0) >= 1
    assert rep.counts.get("MIXED", 0) >= 1

    body = render_html(rep)
    assert "Weekly Sharia Compliance Report" in body
    assert "🟢 شرعي" in body
    assert "🟡 مختلط" in body
    assert "<b>" in body              # HTML tags landed
    assert "*Status" not in body      # no Markdown bold leaked through
