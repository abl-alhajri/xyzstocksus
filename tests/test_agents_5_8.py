"""Commit 10 tests — Devil's Advocate, Macro Voice, Sharia Officer (with veto fast-path), Synthesizer."""
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


def _make_input(sharia_status="HALAL"):
    from agents.base import AgentInput
    return AgentInput(
        symbol="TSLA",
        sector="BTC_TREASURY",
        agent_set="btc_full",
        sharia_status=sharia_status,
        last_price=245.0,
        heuristic={"total": 78},
        technical={"rsi_14": 58.0, "atr_14": 6.0, "last_close": 245.0,
                   "ema_20": 240.0, "ema_50": 230.0,
                   "macd_hist": 0.4, "volume_ratio_20d": 2.5},
        btc_price=84500.0,
        btc_regime="BULL",
        btc_corr_30d=0.65,
        btc_beta=0.7,
        macro_recent=[{"speaker": "Powell", "quote_text": "rates restrictive", "sentiment": "HAWKISH"}],
        sharia_ratios={"debt_ratio": 0.05, "cash_ratio": 0.08, "impermissible_ratio": 0.0,
                       "risk_tier": "GREEN", "drift_warning": False, "filing_date": "2025-09-30"},
    )


def _fake_response(parsed: dict):
    from llm.client import LLMResponse, LLMUsage
    return LLMResponse(
        text=json.dumps(parsed),
        parsed_json=parsed,
        usage=LLMUsage(input_tokens=2000, output_tokens=400, cached_tokens=1500,
                      cost_usd=0.012, model="claude-sonnet-4-6"),
        raw_blocks=[],
    )


def test_devils_advocate_returns_kill_thesis():
    from agents.devils_advocate import DevilsAdvocate
    parsed = {
        "decision": "PASS",
        "confidence": 0.6,
        "rationale": "Hawkish Powell on a high-beta name",
        "structured": {
            "kill_thesis": "Powell hawkish stance crushes high-beta names",
            "primary_risk": "macro",
            "scenarios": ["worst-case dump", "alt: chop"],
        },
    }
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = DevilsAdvocate().run(_make_input())
    assert out.decision == "PASS"
    assert "kill_thesis" in (out.structured.get("structured") or {})


def test_macro_voice_includes_indices():
    from agents.macro_voice import MacroVoice
    parsed = {
        "decision": "HOLD",
        "confidence": 0.5,
        "rationale": "Powell hawkish per macro_recent[0]",
        "structured": {"macro_alignment": "headwind", "fed_stance": "hawkish",
                      "near_term_event_risk": "medium",
                      "key_quotes_indices": [0]},
    }
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = MacroVoice().run(_make_input())
    assert out.decision == "HOLD"
    assert out.structured.get("structured", {}).get("fed_stance") == "hawkish"


def test_sharia_officer_deterministic_veto_on_haram_input():
    """The fast-path skips the LLM entirely when sharia_status is HARAM."""
    from agents.sharia_officer import ShariaOfficer

    with patch("agents.base.complete") as mock_complete:
        out = ShariaOfficer().run(_make_input(sharia_status="HARAM"))
    assert mock_complete.call_count == 0
    assert out.decision == "VETO"
    assert out.confidence == 1.0
    assert out.veto_reason == "Sharia status HARAM"


def test_sharia_officer_calls_llm_for_halal_path():
    from agents.sharia_officer import ShariaOfficer
    parsed = {
        "decision": "HOLD",
        "confidence": 0.85,
        "rationale": "All ratios green",
        "structured": {"status": "HALAL", "summary_arabic": "🟢 شرعي",
                      "debt_tier": "GREEN", "cash_tier": "GREEN",
                      "impermissible_tier": "GREEN", "drift_warning": False},
    }
    with patch("agents.base.complete", return_value=_fake_response(parsed)) as mock_call:
        out = ShariaOfficer().run(_make_input(sharia_status="HALAL"))
    assert mock_call.call_count == 1
    assert out.decision == "HOLD"
    assert out.structured.get("structured", {}).get("summary_arabic") == "🟢 شرعي"


def test_synthesizer_combines_others():
    from agents.synthesizer import Synthesizer
    parsed = {
        "decision": "BUY",
        "trade_type": "SWING",
        "confidence": 0.74,
        "rationale": "Tech + fundamentals + macro neutral aligned long, Sharia HALAL.",
        "veto_reason": None,
        "structured": {
            "entry_zone": [243, 248],
            "stop_loss": 238.0,
            "take_profits": [
                {"label": "TP1", "price": 258, "size_pct": 50},
                {"label": "TP2", "price": 268, "size_pct": 30},
                {"label": "TP3", "price": 278, "size_pct": 20},
            ],
            "risk_reward": "1:2.8",
            "summary": "Strong setup with BTC tailwind",
        },
    }
    with patch("agents.base.complete", return_value=_fake_response(parsed)):
        out = Synthesizer().run(_make_input())
    assert out.decision == "BUY"
    assert out.structured.get("trade_type") == "SWING"
    assert len(out.structured.get("structured", {}).get("take_profits", [])) == 3
