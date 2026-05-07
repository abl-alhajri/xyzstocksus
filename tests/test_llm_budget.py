"""Commit 8 tests — pricing, prompt-cache JSON parsing, budget guard, prescreen sizing."""
from __future__ import annotations

import importlib
import json

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


# ----------------------------- pricing -----------------------------------

def test_pricing_haiku_calculation():
    from llm.client import estimate_cost
    cost = estimate_cost(
        model="claude-haiku-4-5",
        input_tokens=600, output_tokens=120,
        cached_tokens=0, cache_creation_tokens=0,
    )
    # 600 * 1.0 / 1e6 + 120 * 5.0 / 1e6 = 0.0006 + 0.0006 = 0.0012
    assert cost == pytest.approx(0.0012, rel=1e-3)


def test_pricing_sonnet_with_cache_hits():
    from llm.client import estimate_cost
    # 1500 cached + 700 fresh + 500 output
    cost = estimate_cost(
        model="claude-sonnet-4-6",
        input_tokens=2200, output_tokens=500,
        cached_tokens=1500, cache_creation_tokens=0,
    )
    # fresh = 2200 - 1500 = 700  → 700*3 = 2100
    # cached = 1500 * 0.30 = 450
    # output = 500 * 15 = 7500
    # total = 10050 / 1e6 = 0.01005
    assert cost == pytest.approx(0.01005, rel=1e-3)


def test_pricing_unknown_model_uses_default():
    from llm.client import estimate_cost
    cost = estimate_cost(
        model="claude-future-99",
        input_tokens=1000, output_tokens=100,
    )
    # Default = sonnet pricing
    assert cost > 0


# ----------------------------- JSON extraction ---------------------------

def test_json_parser_extracts_fenced():
    from llm.client import _parse_json_block
    text = "Here is my answer:\n```json\n{\"a\": 1}\n```\n"
    assert _parse_json_block(text) == {"a": 1}


def test_json_parser_extracts_bare():
    from llm.client import _parse_json_block
    text = "Random prose.\n{\"decision\": \"BUY\", \"confidence\": 0.7}\nDone."
    assert _parse_json_block(text) == {"decision": "BUY", "confidence": 0.7}


def test_json_parser_returns_none_on_garbage():
    from llm.client import _parse_json_block
    assert _parse_json_block("not json at all") is None


# ----------------------------- budget guard ------------------------------

def test_budget_clean_state_allows_all():
    from core.budget_guard import can_run_deep, can_run_haiku
    ok, _ = can_run_deep()
    assert ok is True
    ok, _ = can_run_haiku()
    assert ok is True


def test_budget_daily_hard_blocks_deep():
    from core.budget_guard import can_run_deep
    from db.repos import costs as costs_repo
    costs_repo.insert_cost(
        model="claude-sonnet-4-6", agent="technical", symbol="TSLA",
        input_tokens=1, output_tokens=1, cached_tokens=0,
        cache_creation_tokens=0, cost_usd=6.00,   # over $5 daily hard
    )
    ok, reason = can_run_deep()
    assert ok is False
    assert "Daily hard cap" in reason


def test_budget_monthly_hard_blocks_everything():
    from core.budget_guard import can_run_deep, can_run_haiku
    from db.repos import costs as costs_repo
    costs_repo.insert_cost(
        model="claude-sonnet-4-6", agent="technical", symbol="TSLA",
        input_tokens=1, output_tokens=1, cached_tokens=0,
        cache_creation_tokens=0, cost_usd=85.00,
    )
    ok, _ = can_run_deep()
    assert ok is False
    ok, _ = can_run_haiku()
    assert ok is False


def test_budget_quick_only_auto_flips_at_75pct():
    from core.budget_guard import reconcile_quick_only_flag, can_run_deep
    from db.repos import costs as costs_repo
    # 75% of $80 = $60 → put $61 in
    costs_repo.insert_cost(
        model="claude-sonnet-4-6", agent="technical", symbol="TSLA",
        input_tokens=1, output_tokens=1, cached_tokens=0,
        cache_creation_tokens=0, cost_usd=61.00,
    )
    flag = reconcile_quick_only_flag()
    assert flag is True
    ok, reason = can_run_deep()
    assert ok is False
    assert "/quick-only" in reason


def test_budget_deep_cap_blocks_after_30():
    from core.budget_guard import can_run_deep
    from db.repos import costs as costs_repo
    for i in range(30):
        costs_repo.insert_cost(
            model="claude-sonnet-4-6", agent="technical", symbol=f"S{i}",
            input_tokens=1, output_tokens=1, cached_tokens=0,
            cache_creation_tokens=0, cost_usd=0.05,
        )
    ok, reason = can_run_deep()
    assert ok is False
    assert "deep-analysis cap" in reason


# ----------------------------- prescreen sizing --------------------------

def test_prescreen_select_caps_at_max():
    from llm.prescreen_haiku import _select_survivors, PrescreenCandidate, PrescreenVerdict
    cands = [
        PrescreenCandidate(symbol=f"S{i}", sector="TECH_LARGE", agent_set="standard",
                           sharia_status="HALAL", heuristic={"total": 80 - i}, last_price=100,
                           btc_regime="BULL", earnings_blackout=False)
        for i in range(8)
    ]
    verdicts = [PrescreenVerdict(c.symbol, True, "ok") for c in cands]
    chosen = _select_survivors(verdicts, cands, min_n=2, max_n=4)
    assert len(chosen) == 4
    # Highest-scoring symbols come first
    assert chosen[0].symbol == "S0"
    assert chosen[-1].symbol == "S3"


def test_prescreen_select_does_not_pad_weak():
    from llm.prescreen_haiku import _select_survivors, PrescreenCandidate, PrescreenVerdict
    # Only 1 strong, the rest are below 50
    cands = [
        PrescreenCandidate(symbol="STRONG", sector="TECH_LARGE", agent_set="standard",
                           sharia_status="HALAL", heuristic={"total": 80}, last_price=100,
                           btc_regime="BULL", earnings_blackout=False),
        PrescreenCandidate(symbol="WEAK1", sector="TECH_LARGE", agent_set="standard",
                           sharia_status="HALAL", heuristic={"total": 40}, last_price=100,
                           btc_regime="NEUTRAL", earnings_blackout=False),
        PrescreenCandidate(symbol="WEAK2", sector="TECH_LARGE", agent_set="standard",
                           sharia_status="HALAL", heuristic={"total": 35}, last_price=100,
                           btc_regime="NEUTRAL", earnings_blackout=False),
    ]
    verdicts = [PrescreenVerdict("STRONG", True, "ok"),
                PrescreenVerdict("WEAK1", False, ""),
                PrescreenVerdict("WEAK2", False, "")]
    chosen = _select_survivors(verdicts, cands, min_n=2, max_n=4)
    # Only 1 strong; min=2 but no other candidate is >= 50, so we accept just 1
    assert len(chosen) == 1
    assert chosen[0].symbol == "STRONG"
