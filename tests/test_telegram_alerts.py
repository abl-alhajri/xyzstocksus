"""Commit 13 tests — alert formatter (offline, no SDK calls)."""
from __future__ import annotations

import pytest


def _make_debate(*, decision="BUY", confidence=0.78, sharia_status="HALAL",
                 vetoed=False, drift=False):
    from agents.debate import DebateResult
    from agents.base import AgentOutput
    from llm.client import LLMUsage

    sharia_out = AgentOutput(
        agent_name="sharia",
        decision="HOLD" if not vetoed else "VETO",
        confidence=0.85,
        rationale="Halal — green tiers" if not vetoed else "Sharia veto",
        structured={"structured": {
            "status": sharia_status,
            "summary_arabic": "🟢 شرعي",
            "drift_warning": drift,
            "as_of_filing": "2025-09-30",
        }},
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
        veto_reason="Sharia status HARAM" if vetoed else None,
    )
    devil_out = AgentOutput(
        agent_name="devils_advocate",
        decision="PASS",
        confidence=0.5,
        rationale="Hawkish Powell on high-beta names",
        structured={"structured": {"kill_thesis": "Hawkish Powell pressure"}},
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
    )
    macro_out = AgentOutput(
        agent_name="macro_voice",
        decision="HOLD",
        confidence=0.55,
        rationale="Powell hawkish hold",
        structured={},
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
    )
    final = AgentOutput(
        agent_name="synthesizer",
        decision=decision,
        confidence=confidence,
        rationale="Aligned long with Sharia OK",
        structured={
            "decision": decision,
            "trade_type": "SWING",
            "confidence": confidence,
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
        },
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
    ) if not vetoed else None

    return DebateResult(
        symbol="TSLA",
        agent_set="standard",
        round1=[sharia_out, devil_out, macro_out],
        round2=[],
        final=final,
        vetoed=vetoed,
        veto_reason="Sharia status HARAM" if vetoed else None,
        total_cost_usd=0.18,
    )


def test_signal_renders_markdown_with_sharia_label():
    from telegram_bot.alerts import render_signal
    text = render_signal(_make_debate(), btc_price=84500.0)
    assert "*NEW SIGNAL — TSLA*" in text
    assert "*Sharia Status:*" in text
    assert "HALAL" in text  # English label
    assert "شرعي" in text   # Arabic label
    assert "TP1" in text and "TP2" in text and "TP3" in text
    assert "$238" in text  # stop loss
    assert "Confidence:* 78%" in text


def test_drift_warning_surfaces():
    from telegram_bot.alerts import render_signal
    text = render_signal(_make_debate(drift=True))
    assert "Drift warning" in text


def test_vetoed_signal_renders_rejected():
    from telegram_bot.alerts import render_signal
    text = render_signal(_make_debate(vetoed=True))
    assert "REJECTED" in text
    assert "Sharia veto" in text


def test_compliance_alert_renders():
    from telegram_bot.alerts import render_compliance_alert
    text = render_compliance_alert("MSTR", "TIER_CHANGE", "YELLOW", "ORANGE")
    assert "MSTR" in text
    assert "YELLOW" in text and "ORANGE" in text
