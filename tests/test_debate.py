"""Commit 11 tests — debate orchestrator: R1 parallel, vetoes, R2 band, R3 synth."""
from __future__ import annotations

import importlib
import json
from unittest.mock import patch

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


def _make_input(symbol="TSLA", sharia_status="HALAL", agent_set="standard",
                sector="TECH_LARGE"):
    from agents.base import AgentInput
    return AgentInput(
        symbol=symbol,
        sector=sector,
        agent_set=agent_set,
        sharia_status=sharia_status,
        last_price=245.0,
        heuristic={"total": 78},
        technical={"rsi_14": 58.0, "atr_14": 6.0, "last_close": 245.0,
                   "ema_20": 240.0, "ema_50": 230.0, "macd_hist": 0.4,
                   "volume_ratio_20d": 2.5},
        btc_price=84500.0, btc_regime="BULL", btc_corr_30d=0.65,
        btc_beta=0.7,
        sharia_ratios={"debt_ratio": 0.05, "cash_ratio": 0.08,
                      "impermissible_ratio": 0.0, "risk_tier": "GREEN",
                      "drift_warning": False, "filing_date": "2025-09-30"},
    )


def _fake_for(decision: str, confidence: float, *, structured=None):
    """Patch every agent's underlying complete() so they all behave the same."""
    from llm.client import LLMResponse, LLMUsage
    parsed = {
        "decision": decision,
        "confidence": confidence,
        "rationale": "test",
        "structured": structured or {"foo": "bar"},
    }
    return LLMResponse(
        text=json.dumps(parsed),
        parsed_json=parsed,
        usage=LLMUsage(input_tokens=2000, output_tokens=400, cached_tokens=1500,
                      cost_usd=0.012, model="claude-sonnet-4-6"),
        raw_blocks=[],
    )


def test_haram_short_circuits_pre_debate():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD

    inp = _make_input(sharia_status="HARAM")
    with patch("agents.base.complete") as mock_complete:
        result = run_debate(inp, STANDARD)
    # Sharia officer takes the deterministic fast path → no LLM calls
    assert mock_complete.call_count == 0
    assert result.vetoed is True
    assert "HARAM" in result.veto_reason


def test_round1_runs_all_set_agents_in_parallel():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD, AGENT_SHARIA, AGENT_SYNTHESIZER

    with patch("agents.base.complete", return_value=_fake_for("BUY", 0.78,
            structured={"trend": "uptrend", "decision": "BUY", "confidence": 0.78,
                       "structured": {"status": "HALAL"}})):
        result = run_debate(_make_input(), STANDARD)

    r1_names = {o.agent_name for o in result.round1}
    expected = STANDARD.agents - {AGENT_SYNTHESIZER}
    assert r1_names == expected
    assert result.final is not None
    assert result.vetoed is False


def test_post_round1_sharia_veto_short_circuits_r2_r3():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD, AGENT_SHARIA
    from llm.client import LLMResponse, LLMUsage

    def per_agent(*args, **kwargs):
        # Identify which agent we're in by inspecting system_parts content
        sys_blocks = kwargs.get("system_parts") or args[2] if len(args) > 2 else []
        try:
            joined = " ".join(t for t, _ in sys_blocks)
        except Exception:
            joined = ""
        if "Sharia Compliance Officer" in joined:
            parsed = {
                "decision": "VETO",
                "confidence": 1.0,
                "rationale": "RED tier",
                "veto_reason": "Debt 35% — clear breach",
                "structured": {"status": "HARAM"},
            }
        else:
            parsed = {"decision": "BUY", "confidence": 0.7,
                      "rationale": "x", "structured": {}}
        return LLMResponse(
            text=json.dumps(parsed), parsed_json=parsed,
            usage=LLMUsage(input_tokens=2000, output_tokens=400,
                          cached_tokens=1500, cost_usd=0.012,
                          model="claude-sonnet-4-6"),
            raw_blocks=[],
        )

    inp = _make_input(sharia_status="MIXED")  # so officer is called via LLM
    with patch("agents.base.complete", side_effect=per_agent):
        result = run_debate(inp, STANDARD)
    assert result.vetoed is True
    assert result.final is None  # synthesizer never ran
    assert result.round2 == []   # cross-critique skipped


def test_r2_fires_in_band():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD

    # peak confidence 0.65 → inside 0.60-0.70 band
    with patch("agents.base.complete",
               return_value=_fake_for("HOLD", 0.65,
                                      structured={"decision": "HOLD",
                                                  "confidence": 0.65,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(_make_input(), STANDARD)
    assert result.round2  # R2 fired


def test_r2_skipped_outside_band():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD

    # peak confidence 0.85 → outside band → no R2
    with patch("agents.base.complete",
               return_value=_fake_for("BUY", 0.85,
                                      structured={"decision": "BUY",
                                                  "confidence": 0.85,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(_make_input(), STANDARD)
    assert result.round2 == []


def test_force_full_mode_runs_r2_regardless():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD

    with patch("agents.base.complete",
               return_value=_fake_for("BUY", 0.85,
                                      structured={"decision": "BUY",
                                                  "confidence": 0.85,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(_make_input(), STANDARD, force_full_mode=True)
    assert result.round2  # forced R2


def test_skip_btc_full_drops_btc_macro_agent():
    from agents.debate import run_debate
    from config.agent_sets import BTC_FULL, AGENT_BTC_MACRO

    inp = _make_input(symbol="MSTR", sector="BTC_TREASURY", agent_set="btc_full")
    with patch("agents.base.complete",
               return_value=_fake_for("BUY", 0.8,
                                      structured={"decision": "BUY",
                                                  "confidence": 0.8,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(inp, BTC_FULL, skip_btc_full=True)
    r1_names = {o.agent_name for o in result.round1}
    assert AGENT_BTC_MACRO not in r1_names


def test_lean_set_runs_5_agents():
    from agents.debate import run_debate
    from config.agent_sets import LEAN, AGENT_SHARIA, AGENT_SYNTHESIZER

    inp = _make_input(symbol="HLAL", sector="HALAL_ETF", agent_set="lean")
    with patch("agents.base.complete",
               return_value=_fake_for("HOLD", 0.55,
                                      structured={"decision": "HOLD",
                                                  "confidence": 0.55,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(inp, LEAN)
    r1_names = {o.agent_name for o in result.round1}
    expected = LEAN.agents - {AGENT_SYNTHESIZER}
    assert r1_names == expected
    assert len(r1_names) == 4  # 5 agents minus synthesizer


def test_total_cost_aggregated_across_rounds():
    from agents.debate import run_debate
    from config.agent_sets import STANDARD

    with patch("agents.base.complete",
               return_value=_fake_for("HOLD", 0.65,
                                      structured={"decision": "HOLD",
                                                  "confidence": 0.65,
                                                  "structured": {"status": "HALAL"}})):
        result = run_debate(_make_input(), STANDARD)
    # R1 (6 non-synth, non-sharia agents at 0.012 each + sharia LLM call)
    # + R2 (~6 critique agents) + Synthesizer
    # Just verify the number is non-zero and reasonable
    assert result.total_cost_usd > 0.05
    assert result.total_cost_usd < 1.0  # sanity
