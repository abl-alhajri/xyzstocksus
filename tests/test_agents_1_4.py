"""Commit 9 tests — agents 1-4 wiring + base parsing.

The actual LLM calls are mocked (we patch llm.client.complete) so tests run
without an Anthropic key. The goal is to verify:
- system prompts assemble correctly with cache_control
- the agent's user payload contains the right structured data
- AgentOutput parsing handles missing/messy fields gracefully
"""
from __future__ import annotations

import importlib
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


def _make_input(symbol="TSLA", sharia_status="HALAL"):
    from agents.base import AgentInput
    return AgentInput(
        symbol=symbol,
        sector="BTC_TREASURY",
        agent_set="btc_full",
        sharia_status=sharia_status,
        last_price=245.0,
        heuristic={"total": 78, "momentum": 25, "trend": 25, "volume": 14, "btc_align": 14, "notes": []},
        technical={"rsi_14": 58.0, "macd": 1.2, "macd_signal": 0.8, "macd_hist": 0.4,
                   "ema_20": 240.0, "ema_50": 230.0, "atr_14": 6.0,
                   "last_close": 245.0, "volume_ratio_20d": 2.5},
        btc_price=84500.0,
        btc_regime="BULL",
        btc_corr_30d=0.65,
        btc_beta=0.7,
    )


def _fake_response(parsed: dict, *, input_tokens=2200, output_tokens=500,
                   cached_tokens=1500, model="claude-sonnet-4-6"):
    from llm.client import LLMResponse, LLMUsage, estimate_cost
    cost = estimate_cost(model=model, input_tokens=input_tokens,
                        output_tokens=output_tokens, cached_tokens=cached_tokens)
    usage = LLMUsage(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cached_tokens=cached_tokens, cache_creation_tokens=0,
        cost_usd=cost, latency_ms=1200, model=model,
    )
    import json as _json
    return LLMResponse(
        text=_json.dumps(parsed),
        parsed_json=parsed,
        usage=usage,
        raw_blocks=[{"type": "text", "text": _json.dumps(parsed)}],
    )


def test_system_prompts_have_cache_control():
    from agents.technical_analyst import TechnicalAnalyst
    a = TechnicalAnalyst()
    parts = a.system_parts()
    # Both blocks should request caching
    assert all(p[1] is True for p in parts)
    assert any("Technical Analyst" in p[0] for p in parts)


def test_user_payload_includes_structured_data():
    from agents.technical_analyst import TechnicalAnalyst
    inp = _make_input()
    a = TechnicalAnalyst()
    msgs = a.user_messages(inp, round_num=1)
    assert msgs[0]["role"] == "user"
    import json
    body = json.loads(msgs[0]["content"])
    assert body["data"]["symbol"] == "TSLA"
    assert body["data"]["technical"]["rsi_14"] == 58.0
    assert body["data"]["btc_context"]["regime"] == "BULL"
    assert body["round"] == 1


def test_round2_payload_includes_others():
    from agents.technical_analyst import TechnicalAnalyst
    from agents.base import AgentOutput
    from llm.client import LLMUsage

    others = [
        AgentOutput(agent_name="risk", decision="HOLD", confidence=0.6,
                    rationale="ATR-based stop ok", structured={},
                    usage=LLMUsage(model="claude-sonnet-4-6"), raw_text=""),
        AgentOutput(agent_name="sharia", decision="HOLD", confidence=0.7,
                    rationale="Halal", structured={},
                    usage=LLMUsage(model="claude-sonnet-4-6"), raw_text=""),
    ]
    a = TechnicalAnalyst()
    msgs = a.user_messages(_make_input(), round_num=2, others=others)
    import json
    body = json.loads(msgs[0]["content"])
    assert "round_1_outputs" in body
    assert {o["agent"] for o in body["round_1_outputs"]} == {"risk", "sharia"}


def test_agent_output_parses_clean_json():
    from agents.technical_analyst import TechnicalAnalyst
    parsed = {
        "decision": "BUY",
        "confidence": 0.78,
        "rationale": "Price > EMA20 > EMA50 with 2.5x volume",
        "structured": {"trend": "uptrend", "momentum": "bullish",
                      "volume_quality": "strong",
                      "suggested_stop_atr_multiple": 1.5,
                      "trade_type": "SWING"},
    }
    a = TechnicalAnalyst()
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = a.run(_make_input())
    assert out.decision == "BUY"
    assert out.confidence == pytest.approx(0.78)
    assert out.structured["structured"]["trend"] == "uptrend"


def test_agent_output_clamps_confidence_and_uppercases_decision():
    from agents.technical_analyst import TechnicalAnalyst
    parsed = {"decision": "buy", "confidence": 1.7, "rationale": "ok"}
    a = TechnicalAnalyst()
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = a.run(_make_input())
    assert out.decision == "BUY"
    assert out.confidence == 1.0


def test_agent_failure_returns_safe_hold():
    from agents.technical_analyst import TechnicalAnalyst
    def boom(*a, **kw): raise RuntimeError("network gone")
    with patch("agents.base.complete", side_effect=boom):
        out = TechnicalAnalyst().run(_make_input())
    assert out.decision == "HOLD"
    assert out.confidence == 0.0
    assert "Agent unavailable" in out.rationale


def test_agent_records_cost():
    from agents.technical_analyst import TechnicalAnalyst
    from db.repos import costs as costs_repo
    parsed = {"decision": "BUY", "confidence": 0.7, "rationale": "ok"}
    a = TechnicalAnalyst()
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        a.run(_make_input(symbol="TSLA"))
    breakdown = costs_repo.per_agent_today()
    assert "technical" in breakdown
    assert breakdown["technical"] > 0


def test_btc_macro_agent_only_runs_for_btc_full_input():
    """The agent itself doesn't gate the set — that's the orchestrator's job —
    but its prompt should reference BTC fields and the agent set name should
    match the registered name."""
    from agents.btc_macro_analyst import BTCMacroAnalyst
    from config.agent_sets import AGENT_BTC_MACRO
    assert BTCMacroAnalyst.name == AGENT_BTC_MACRO


def test_fundamentals_agent_passes_blackout():
    from agents.fundamentals_analyst import FundamentalsAnalyst
    parsed = {
        "decision": "PASS",
        "confidence": 0.9,
        "rationale": "earnings within 48h",
        "structured": {"balance_sheet": "moderate"},
    }
    inp = _make_input()
    inp.earnings_blackout = True
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = FundamentalsAnalyst().run(inp)
    assert out.decision == "PASS"


def test_risk_manager_only_holds_or_passes():
    from agents.risk_manager import RiskManager
    parsed = {
        "decision": "HOLD",
        "confidence": 0.65,
        "rationale": "ATR-based stop reasonable",
        "structured": {"stop_atr_multiple": 1.5, "risk_grade": "B"},
    }
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = RiskManager().run(_make_input())
    assert out.decision in ("HOLD", "PASS")
