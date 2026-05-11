"""Commit 2 tests — config layer (watchlist, exclusions, agent sets, thresholds)."""
from __future__ import annotations

from config.agent_sets import (
    AGENT_BTC_MACRO,
    AGENT_SHARIA,
    AGENT_SYNTHESIZER,
    BTC_FULL,
    LEAN,
    STANDARD,
    resolve_set_for_sector,
)
from config.excluded_stocks import EXCLUDED, is_excluded
from config.thresholds import (
    DEBT_MAX_RATIO,
    R2_BAND,
    DRIFT_PP_PER_QUARTER,
    DRIFT_PROXIMITY_PP,
    RiskTier,
    ShariaStatus,
    classify_ratio,
    derive_status,
    is_drift_warning,
)
from config.watchlist import WATCHLIST, all_symbols, get_seed


# ----------------------- Watchlist -----------------------

def test_watchlist_size_matches_spec():
    # 43 stocks + 3 halal ETFs after migration 005 watchlist refresh
    # (removed 19, added 16 net new — see db/migrations/005_watchlist_refresh.sql)
    assert len(WATCHLIST) == 46


def test_watchlist_no_overlap_with_excluded():
    overlap = set(WATCHLIST.keys()) & set(EXCLUDED.keys())
    assert overlap == set(), f"watchlist overlaps excluded: {overlap}"


def test_watchlist_seed_lookup_case_insensitive():
    assert get_seed("tsla") is not None
    assert get_seed("TSLA").sector == "BTC_TREASURY"


def test_btc_beta_realistic():
    for sym, seed in WATCHLIST.items():
        assert 0.0 <= seed.btc_beta <= 5.0, f"{sym} btc_beta {seed.btc_beta} out of range"


def test_watchlist_removed_tickers_absent():
    removed = (
        "MARA", "DIS", "MCD", "NKE", "PEP", "PYPL", "COIN", "TMO", "PFE",
        "LLY", "COST", "CAT", "META", "MA", "AVGO", "MSFT", "UNH", "V", "HD",
    )
    for sym in removed:
        assert sym not in WATCHLIST, f"{sym} should have been removed in migration 005"


def test_watchlist_new_tickers_present():
    added = (
        "TSM", "TXN", "PLTR", "MRVL", "STX", "ARM", "VRT", "VST", "TLN",
        "ANET", "AMAT", "WDC", "CRWD", "NOW", "COHR", "ON",
    )
    for sym in added:
        assert sym in WATCHLIST, f"{sym} should have been added in migration 005"


def test_watchlist_no_haram_seeds():
    # Seed hints are not authoritative, but no entry should claim HARAM —
    # if business activity is a hard exclusion it belongs in EXCLUDED, not the watchlist.
    for sym, seed in WATCHLIST.items():
        assert seed.expected_status != "HARAM", f"{sym} should not be seeded as HARAM"


def test_watchlist_new_categories_populated():
    sectors = {s.sector for s in WATCHLIST.values()}
    for required in ("AI_INFRA", "STORAGE", "UTILITIES", "SEMICONDUCTORS"):
        assert required in sectors, f"missing sector: {required}"


def test_new_tickers_landed_in_expected_categories():
    """16 net new tickers must land in the categories declared in the refresh plan."""
    expected = {
        "TSM": "SEMICONDUCTORS", "TXN": "SEMICONDUCTORS",
        "MRVL": "SEMICONDUCTORS", "ARM": "SEMICONDUCTORS",
        "AMAT": "SEMICONDUCTORS", "COHR": "SEMICONDUCTORS",
        "ON": "SEMICONDUCTORS",
        "PLTR": "AI_INFRA", "VRT": "AI_INFRA", "ANET": "AI_INFRA",
        "CRWD": "AI_INFRA", "NOW": "AI_INFRA",
        "STX": "STORAGE", "WDC": "STORAGE",
        "VST": "UTILITIES", "TLN": "UTILITIES",
    }
    for sym, sector in expected.items():
        seed = WATCHLIST.get(sym)
        assert seed is not None, f"{sym} missing"
        assert seed.sector == sector, (
            f"{sym} sector {seed.sector!r} != expected {sector!r}"
        )


def test_watchlist_empty_categories_absent():
    sectors = {s.sector for s in WATCHLIST.values()}
    for dropped in ("CRYPTO_EXCHANGE", "HEALTHCARE_DEVICE",
                    "HEALTHCARE_INSURE", "FINANCE_PAYMENT"):
        assert dropped not in sectors, f"dead sector still present: {dropped}"


# ----------------------- Exclusions -----------------------

def test_excluded_banks_blocked():
    for sym in ("JPM", "BAC", "WFC", "GS", "MS"):
        assert is_excluded(sym), f"{sym} should be excluded"


def test_excluded_btc_futures_etfs():
    for sym in ("IBIT", "FBTC", "GBTC", "BITO"):
        assert is_excluded(sym)


def test_excluded_broad_etfs():
    for sym in ("SPY", "QQQ", "IWM", "DIA", "VTI"):
        assert is_excluded(sym)


# ----------------------- Agent sets -----------------------

def test_sharia_in_every_set():
    for s in (BTC_FULL, STANDARD, LEAN):
        assert AGENT_SHARIA in s.agents, f"Sharia missing from {s.name}"
        assert AGENT_SYNTHESIZER in s.agents, f"Synthesizer missing from {s.name}"


def test_btc_macro_only_in_btc_full():
    assert AGENT_BTC_MACRO in BTC_FULL.agents
    assert AGENT_BTC_MACRO not in STANDARD.agents
    assert AGENT_BTC_MACRO not in LEAN.agents


def test_set_sizes():
    assert len(BTC_FULL.agents) == 8
    assert len(STANDARD.agents) == 7
    assert len(LEAN.agents) == 5


def test_resolver_btc():
    for sector in ("BTC_TREASURY", "BTC_MINER", "CRYPTO_ADJACENT", "MINING_HARDWARE"):
        assert resolve_set_for_sector(sector).name == "btc_full"


def test_resolver_etf():
    assert resolve_set_for_sector("HALAL_ETF").name == "lean"
    assert resolve_set_for_sector("HALAL_SUKUK").name == "lean"


def test_resolver_default_standard():
    assert resolve_set_for_sector("TECH_MEGA").name == "standard"
    assert resolve_set_for_sector("HEALTHCARE_PHARMA").name == "standard"
    assert resolve_set_for_sector("UNKNOWN_SECTOR").name == "standard"


# ----------------------- Thresholds -----------------------

def test_classify_ratio_tiers():
    assert classify_ratio(0.10) == RiskTier.GREEN
    assert classify_ratio(0.249) == RiskTier.GREEN
    assert classify_ratio(0.25) == RiskTier.YELLOW
    assert classify_ratio(0.299) == RiskTier.YELLOW
    assert classify_ratio(0.30) == RiskTier.ORANGE
    assert classify_ratio(0.329) == RiskTier.ORANGE
    assert classify_ratio(0.33) == RiskTier.RED
    assert classify_ratio(0.50) == RiskTier.RED


def test_classify_ratio_unknown_is_yellow():
    assert classify_ratio(None) == RiskTier.YELLOW


def test_derive_status_halal():
    bd = derive_status(debt_ratio=0.10, cash_ratio=0.12, impermissible_ratio=0.005)
    assert bd.overall == ShariaStatus.HALAL


def test_derive_status_mixed_borderline():
    bd = derive_status(debt_ratio=0.31, cash_ratio=0.20, impermissible_ratio=0.02)
    assert bd.overall == ShariaStatus.MIXED


def test_derive_status_haram_on_red():
    # Debt at 35% — clear breach
    bd = derive_status(debt_ratio=0.35, cash_ratio=0.10, impermissible_ratio=0.01)
    assert bd.overall == ShariaStatus.HARAM
    assert bd.debt_tier == RiskTier.RED


def test_derive_status_haram_on_impermissible():
    bd = derive_status(debt_ratio=0.10, cash_ratio=0.10, impermissible_ratio=0.06)
    assert bd.overall == ShariaStatus.HARAM


def test_r2_band_refined():
    # Refined to 0.60-0.70 (was 0.55-0.75)
    assert R2_BAND == (0.60, 0.70)


def test_drift_radar_triggers_when_rising_and_near_breach():
    # Series rising fast (~3pp/quarter) and currently 28% (within 3pp of 30%)
    quarterly = [0.19, 0.22, 0.25, 0.28]
    assert is_drift_warning(quarterly, current_ratio=0.28, threshold=DEBT_MAX_RATIO)


def test_drift_radar_silent_when_far_from_breach():
    quarterly = [0.05, 0.07, 0.10, 0.13]
    assert not is_drift_warning(quarterly, current_ratio=0.13, threshold=DEBT_MAX_RATIO)


def test_drift_radar_silent_when_stable():
    quarterly = [0.27, 0.27, 0.28, 0.28]
    assert not is_drift_warning(quarterly, current_ratio=0.28, threshold=DEBT_MAX_RATIO)


def test_drift_radar_silent_with_short_history():
    assert not is_drift_warning([0.20, 0.25], current_ratio=0.25, threshold=DEBT_MAX_RATIO)


def test_drift_constants():
    assert DRIFT_PP_PER_QUARTER == 0.02
    assert DRIFT_PROXIMITY_PP == 0.03
