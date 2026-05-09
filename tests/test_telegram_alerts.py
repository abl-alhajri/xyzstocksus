"""Commit 13 tests — alert formatter (offline, no SDK calls).

Updated when render_signal switched from Markdown V1 to HTML to fix the
400 Bad Request errors caused by unbalanced LLM-supplied chars.
"""
from __future__ import annotations

import asyncio

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


def test_signal_renders_html_with_sharia_label():
    from telegram_bot.alerts import render_signal
    text = render_signal(_make_debate(), btc_price=84500.0)
    assert "<b>NEW SIGNAL — TSLA</b>" in text
    assert "<b>Sharia Status:</b>" in text
    assert "HALAL" in text
    assert "شرعي" in text
    assert "TP1" in text and "TP2" in text and "TP3" in text
    assert "$238" in text  # stop loss now has $ prefix
    assert "Confidence:</b> 78%" in text


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


def test_take_profit_prices_have_dollar_prefix():
    from telegram_bot.alerts import render_signal
    text = render_signal(_make_debate())
    assert "$258" in text and "$268" in text and "$278" in text


def test_special_chars_in_llm_output_are_escaped():
    """Unbalanced _, *, [, < in LLM text must not break the HTML parser.

    Markdown V1 (the previous format) had no escape sequence — a stray `_`
    from a snake_case identifier in the synthesizer summary was enough to
    fail the entire send with 400 Bad Request. Under HTML we just need to
    escape `<`, `>`, `&`.
    """
    from telegram_bot.alerts import render_signal

    d = _make_debate()
    d.final.structured["structured"]["summary"] = (
        "Watch for breakout_setup at *240*, then [BUY] — "
        "<script>alert(1)</script> & {check}"
    )
    d.round1[1].structured["structured"]["kill_thesis"] = (
        "Hawkish_Powell & [META] risk"
    )
    # macro_voice rationale also flows into the message
    d.round1[2] = type(d.round1[2])(
        agent_name="macro_voice",
        decision="HOLD",
        confidence=0.5,
        rationale="Powell_pivot delayed; rate < 5% target",
        structured={},
        usage=d.round1[2].usage,
        raw_text="",
    )

    text = render_signal(d, btc_price=84500.0)

    # Parser-breakers must be neutralised
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text  # ampersand escaped
    # Underscores, asterisks, brackets are fine in HTML — pass through
    assert "breakout_setup" in text
    assert "[BUY]" in text
    assert "Hawkish_Powell" in text


def test_long_synthesis_caps_at_400_chars():
    """The synthesis section caps at 400 chars per the formatter contract.

    This keeps the whole alert well under Telegram's 4096-char hard limit
    without needing send-side truncation in the common case.
    """
    from telegram_bot.alerts import render_signal
    d = _make_debate()
    d.final.structured["structured"]["summary"] = "X" * 5000
    text = render_signal(d, btc_price=84500.0)
    assert "X" * 400 in text
    assert "X" * 401 not in text
    assert len(text) < 2000


def test_send_text_truncates_oversized_message(monkeypatch):
    """send_text must defensively cap at 3800 chars (+ truncation suffix)."""
    from telegram_bot import bot as botmod

    class FakeSettings:
        telegram_bot_token = "fake-token"
        telegram_chat_id = "123"

    captured: dict = {}

    class FakeMsg:
        message_id = 7

    class FakeBot:
        def __init__(self, token):
            pass

        async def send_message(self, **kw):
            captured.update(kw)
            return FakeMsg()

    import telegram
    monkeypatch.setattr(botmod, "settings", FakeSettings)
    monkeypatch.setattr(telegram, "Bot", FakeBot)

    long_text = "Y" * 5000
    result = asyncio.run(botmod.send_text(long_text))

    assert result == 7
    assert len(captured["text"]) <= 3900
    assert captured["text"].endswith("…(truncated)")


def test_send_text_falls_back_to_plain_on_bad_request(monkeypatch):
    """When Telegram rejects HTML markup, send_text retries as plain text."""
    from telegram_bot import bot as botmod
    from telegram.error import BadRequest

    class FakeSettings:
        telegram_bot_token = "fake-token"
        telegram_chat_id = "123"

    calls: list[dict] = []

    class FakeMsg:
        message_id = 9

    class FakeBot:
        def __init__(self, token):
            pass

        async def send_message(self, **kw):
            calls.append(kw)
            if kw.get("parse_mode"):
                raise BadRequest("Can't parse entities: byte offset 42")
            return FakeMsg()

    import telegram
    monkeypatch.setattr(botmod, "settings", FakeSettings)
    monkeypatch.setattr(telegram, "Bot", FakeBot)

    result = asyncio.run(botmod.send_text("hello <broken html"))

    assert result == 9
    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "HTML"
    assert calls[1]["parse_mode"] is None
