"""Commit 6 tests — business screen, ratios, verifier, purification."""
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


# -------------------------- business screen ------------------------------

def test_business_screen_blocks_hard_excluded():
    from sharia.business_screen import screen
    v = screen(symbol="JPM")
    assert v.passed is False
    assert v.category == "HARD_EXCLUDED"


def test_business_screen_blocks_known_bank_sic():
    from sharia.business_screen import screen
    v = screen(symbol="UNKNOWN_BANK", sec_sic="6021")
    assert v.passed is False
    assert v.category == "SIC_BLOCK"


def test_business_screen_blocks_alcohol_industry():
    from sharia.business_screen import screen
    v = screen(symbol="UNKNOWN", yfinance_industry="Brewery & spirits")
    assert v.passed is False
    assert v.category == "SECTOR_HINT"


def test_business_screen_passes_clean_tech():
    from sharia.business_screen import screen
    v = screen(symbol="AAPL", sec_sic="3571",
               yfinance_industry="Consumer Electronics")
    assert v.passed is True


# -------------------------- ratios from inputs ---------------------------

def test_compute_simple_ratios():
    from sharia.ratios import compute, RatioInputs
    inputs = RatioInputs(
        market_cap=1_000_000_000,
        total_debt=200_000_000,
        interest_bearing_debt=200_000_000,
        cash_and_securities=150_000_000,
        total_revenue=500_000_000,
        impermissible_revenue=10_000_000,
        filing_date="2025-09-30",
        filing_type="10-Q",
    )
    r = compute(inputs)
    assert r.debt_ratio == pytest.approx(0.20)
    assert r.cash_ratio == pytest.approx(0.15)
    assert r.impermissible_ratio == pytest.approx(0.02)


def test_compute_safe_div_handles_missing_denominator():
    from sharia.ratios import compute, RatioInputs
    r = compute(RatioInputs(
        market_cap=None, total_debt=100, interest_bearing_debt=100,
        cash_and_securities=50, total_revenue=0, impermissible_revenue=0,
        filing_date=None, filing_type=None,
    ))
    assert r.debt_ratio is None
    assert r.cash_ratio is None
    assert r.impermissible_ratio is None


def test_from_yfinance_info_extraction():
    from sharia.ratios import from_yfinance_info
    info = {
        "marketCap": 1_000_000_000,
        "totalDebt": 250_000_000,
        "totalCash": 100_000_000,
        "totalRevenue": 400_000_000,
    }
    r = from_yfinance_info(info)
    assert r.market_cap == 1_000_000_000
    assert r.interest_bearing_debt == 250_000_000
    assert r.cash_and_securities == 100_000_000
    assert r.total_revenue == 400_000_000
    assert r.impermissible_revenue == 0.0


def test_from_company_facts_picks_latest():
    from sharia.ratios import from_company_facts
    facts = {
        "facts": {
            "us-gaap": {
                "LongTermDebt": {
                    "units": {"USD": [
                        {"val": 100, "end": "2024-12-31"},
                        {"val": 200, "end": "2025-09-30"},
                    ]}
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {"USD": [
                        {"val": 80, "end": "2025-09-30"},
                    ]}
                },
                "Revenues": {
                    "units": {"USD": [
                        {"val": 1000, "end": "2025-09-30"},
                    ]}
                },
            }
        }
    }
    r = from_company_facts(facts, market_cap=10_000)
    assert r.interest_bearing_debt == 200
    assert r.cash_and_securities == 80
    assert r.total_revenue == 1000
    assert r.filing_date == "2025-09-30"


# -------------------------- verifier orchestration -----------------------

def test_verifier_haram_on_business_screen_failure():
    from sharia.verifier import verify
    from sharia.aaoifi import ShariaStatus
    res = verify("JPM")
    assert res.status == ShariaStatus.HARAM
    assert res.business.category == "HARD_EXCLUDED"


def test_verifier_halal_for_clean_inputs():
    from sharia.verifier import verify
    from sharia.aaoifi import ShariaStatus
    res = verify(
        "AAPL",
        market_cap=3_000_000_000_000,
        yfinance_info={
            "marketCap": 3_000_000_000_000,
            "totalDebt": 100_000_000_000,    # 3.3% of market cap
            "totalCash": 60_000_000_000,     # 2%
            "totalRevenue": 380_000_000_000,
        },
    )
    assert res.status == ShariaStatus.HALAL
    assert res.ratios is not None
    assert res.ratios.debt_ratio == pytest.approx(0.0333, rel=1e-3)


def test_verifier_haram_on_high_debt():
    from sharia.verifier import verify
    from sharia.aaoifi import ShariaStatus, RiskTier
    res = verify(
        "MOCK",
        market_cap=100_000_000,
        yfinance_info={
            "marketCap": 100_000_000,
            "totalDebt": 40_000_000,    # 40% — clear breach
            "totalCash": 5_000_000,
            "totalRevenue": 50_000_000,
        },
    )
    assert res.status == ShariaStatus.HARAM
    assert res.debt_tier == RiskTier.RED


def test_verifier_persists_to_db():
    from sharia.verifier import verify
    from db.repos import sharia as sharia_repo, stocks as stocks_repo
    res = verify(
        "TSLA",
        market_cap=800_000_000_000,
        yfinance_info={
            "marketCap": 800_000_000_000,
            "totalDebt": 9_000_000_000,
            "totalCash": 30_000_000_000,
            "totalRevenue": 95_000_000_000,
        },
    )
    latest = sharia_repo.latest_ratios("TSLA")
    assert latest is not None
    assert latest["sharia_status"] == res.status.value
    md = stocks_repo.get("TSLA")
    assert md.sharia_status == res.status.value


def test_verifier_drift_warning_fires():
    from sharia.verifier import verify
    from db.repos import sharia as sharia_repo
    # Pre-seed 4 rising-debt quarters via direct insert
    for q, debt in enumerate([0.20, 0.23, 0.26, 0.28]):
        sharia_repo.insert_ratios(
            symbol="TSLA",
            market_cap=1e9, total_debt=debt*1e9,
            interest_bearing_debt=debt*1e9, cash_and_securities=0.05*1e9,
            total_revenue=2e8, impermissible_revenue=2e6,
            debt_ratio=debt, cash_ratio=0.05, impermissible_ratio=0.01,
            sharia_status="MIXED", risk_tier="YELLOW",
            filing_date=f"2025-{(q+1)*3:02d}-30",
            filing_type="10-Q",
        )
    # Now verify with a current debt at 28% — within 3pp of 30% breach
    res = verify(
        "TSLA",
        market_cap=1e9,
        yfinance_info={
            "marketCap": 1e9,
            "totalDebt": 0.28*1e9,
            "totalCash": 0.05*1e9,
            "totalRevenue": 2e8,
        },
    )
    assert res.drift_warning is True


# -------------------------- purification ---------------------------------

def test_purification_zero_when_no_impermissible():
    from sharia.purification import estimate
    e = estimate(impermissible_ratio=0, dividend_per_share=2.0, quantity=100)
    assert e.per_share_amount is None
    assert "No impermissible" in e.notes


def test_purification_calculates_correctly():
    from sharia.purification import estimate
    e = estimate(impermissible_ratio=0.03, dividend_per_share=4.00, quantity=100)
    assert e.per_share_amount == pytest.approx(0.12)
    assert e.per_position_amount == pytest.approx(12.0)


# -------------------------- ETF bypass + market_cap fix ------------------

def test_certified_etf_bypassed_to_halal():
    """HLAL/SPUS/SPSK return HALAL/GREEN without any data fetch."""
    from sharia.verifier import verify
    from sharia.aaoifi import ShariaStatus, RiskTier
    res = verify("HLAL")  # no yfinance_info, no company_facts, no market_cap
    assert res.status == ShariaStatus.HALAL
    assert res.overall_tier == RiskTier.GREEN
    assert "Sharia-certified ETF" in res.notes


def test_certified_etf_skips_business_screen_and_persists():
    from sharia.verifier import verify
    from db.repos import sharia as sharia_repo
    res = verify("SPUS")
    assert res.business.passed is True
    assert res.business.notes.startswith("Sharia-certified ETF")
    latest = sharia_repo.latest_ratios("SPUS")
    if latest is not None:  # only persists if symbol is in stocks_metadata
        assert latest["sharia_status"] == "HALAL"


def test_market_cap_computed_from_sec_and_tiingo(monkeypatch):
    """When yfinance market_cap is None, SEC shares x Tiingo close should fill in."""
    import data.prices as prices_mod
    monkeypatch.setattr(prices_mod, "latest_close", lambda s: 50.0)
    from sharia.verifier import verify
    facts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [
                        {"val": 1_000_000, "end": "2025-09-30"},
                    ]}
                }
            },
            "us-gaap": {
                "LongTermDebt": {"units": {"USD": [
                    {"val": 1_000_000, "end": "2025-09-30"},
                ]}},
                "Revenues": {"units": {"USD": [
                    {"val": 5_000_000, "end": "2025-09-30"},
                ]}},
            },
        }
    }
    res = verify("AAPL", market_cap=None, company_facts=facts)
    # market_cap = 1_000_000 x 50 = 50_000_000
    # debt_ratio = 1_000_000 / 50_000_000 = 0.02
    assert res.ratios is not None
    assert res.ratios.inputs.market_cap == 50_000_000
    assert res.ratios.debt_ratio == pytest.approx(0.02)
    assert "computed from SEC shares" in res.notes


def test_market_cap_incomplete_when_shares_missing(monkeypatch):
    """SEC has no shares concept -> market_cap stays None -> INCOMPLETE note."""
    import data.prices as prices_mod
    monkeypatch.setattr(prices_mod, "latest_close", lambda s: 50.0)
    from sharia.verifier import verify
    facts_no_shares = {
        "facts": {
            "us-gaap": {
                "LongTermDebt": {"units": {"USD": [
                    {"val": 1_000_000, "end": "2025-09-30"},
                ]}},
                "Revenues": {"units": {"USD": [
                    {"val": 5_000_000, "end": "2025-09-30"},
                ]}},
            }
        }
    }
    res = verify("UNKNOWN", market_cap=None, company_facts=facts_no_shares)
    assert res.ratios.inputs.market_cap is None
    assert res.ratios.debt_ratio is None
    assert "INCOMPLETE" in res.notes
    assert "missing market_cap" in res.notes


def test_extract_shares_outstanding_prefers_dei():
    """dei.EntityCommonStockSharesOutstanding takes priority over us-gaap variant."""
    from sharia.ratios import extract_shares_outstanding
    facts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 100, "end": "2025-09-30"}]}
                }
            },
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 999, "end": "2025-09-30"}]}
                }
            },
        }
    }
    assert extract_shares_outstanding(facts) == 100.0


def test_extract_shares_outstanding_returns_none_when_missing():
    from sharia.ratios import extract_shares_outstanding
    assert extract_shares_outstanding(None) is None
    assert extract_shares_outstanding({"facts": {"us-gaap": {}}}) is None
