"""Tests for the end-of-scan Telegram push (`_maybe_push_signal_async`).

Scheduled scans previously persisted signals to SQLite but emitted nothing
to Telegram — every signal since launch silently disappeared. The helper
now gates on confidence + Sharia + alerts_paused + dedup before pushing.
"""
from __future__ import annotations

import asyncio

import pytest


def _debate(*, decision="BUY", confidence=0.78, sharia_status="HALAL",
            vetoed=False, symbol="TSLA"):
    """Minimal DebateResult fake — same shape as test_telegram_alerts."""
    from agents.debate import DebateResult
    from agents.base import AgentOutput
    from llm.client import LLMUsage

    sharia_out = AgentOutput(
        agent_name="sharia",
        decision="HOLD" if not vetoed else "VETO",
        confidence=0.85,
        rationale="",
        structured={"structured": {"status": sharia_status}},
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
        veto_reason="HARAM" if vetoed else None,
    )
    final = None if vetoed else AgentOutput(
        agent_name="synthesizer",
        decision=decision,
        confidence=confidence,
        rationale="",
        structured={
            "decision": decision,
            "trade_type": "SWING",
            "structured": {
                "entry_zone": [240, 245],
                "stop_loss": 235.0,
                "take_profits": [{"label": "TP1", "price": 255, "size_pct": 50}],
                "risk_reward": "1:2",
                "summary": "ok",
            },
        },
        usage=LLMUsage(model="claude-sonnet-4-6"),
        raw_text="",
    )
    return DebateResult(
        symbol=symbol,
        agent_set="standard",
        round1=[sharia_out],
        round2=[],
        final=final,
        vetoed=vetoed,
        veto_reason="HARAM" if vetoed else None,
        total_cost_usd=0.0,
    )


def _patch_push_deps(monkeypatch, *, alerts_paused=False, dedup_match=False,
                     send_returns=42):
    """Patch every external dep of _maybe_push_signal_async.

    Returns a `captured` dict with `text`, `send_calls`, `mark_sent_calls`.
    """
    from core import orchestrator
    from db.repos import runtime_config, signals as signals_repo
    from telegram_bot import bot as botmod

    captured: dict = {"text": None, "send_calls": 0, "mark_sent_calls": []}

    async def fake_send_text(text, **_kw):
        captured["text"] = text
        captured["send_calls"] += 1
        return send_returns

    def fake_mark_sent(sid, msg_id):
        captured["mark_sent_calls"].append((sid, msg_id))

    def fake_get_value(key, default=None):
        if key == "alerts_paused":
            return alerts_paused
        return default

    def fake_should_dedup(symbol, **_kw):
        return dedup_match

    monkeypatch.setattr(botmod, "send_text", fake_send_text)
    monkeypatch.setattr(signals_repo, "mark_sent", fake_mark_sent)
    monkeypatch.setattr(signals_repo, "should_dedup", fake_should_dedup)
    monkeypatch.setattr(runtime_config, "get_value", fake_get_value)
    return captured, orchestrator


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- happy path

def test_buy_halal_above_threshold_pushes(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(decision="BUY", confidence=0.78, sharia_status="HALAL",
                symbol="TSLA")
    _run(orch._maybe_push_signal_async(d, sid=101, btc_price=84500.0))

    assert captured["send_calls"] == 1
    assert "TSLA" in captured["text"]
    assert "<b>NEW SIGNAL" in captured["text"]  # HTML, not Markdown
    assert captured["mark_sent_calls"] == [(101, 42)]


# ---------------------------------------------------------------- gating

def test_hold_decision_does_not_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(decision="HOLD", confidence=0.78)
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0
    assert captured["mark_sent_calls"] == []


def test_pass_decision_does_not_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(decision="PASS", confidence=0.78)
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_low_confidence_does_not_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    # MIN_CONFIDENCE_FOR_ALERT = 0.65; this is just under
    d = _debate(decision="BUY", confidence=0.64)
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_at_threshold_pushes(monkeypatch):
    """Boundary check: confidence == MIN_CONFIDENCE_FOR_ALERT must push."""
    captured, orch = _patch_push_deps(monkeypatch)
    from config.thresholds import MIN_CONFIDENCE_FOR_ALERT
    d = _debate(decision="BUY", confidence=MIN_CONFIDENCE_FOR_ALERT)
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 1


def test_haram_does_not_push(monkeypatch):
    """Defence-in-depth: even a non-vetoed BUY must be HALAL to push."""
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(decision="BUY", confidence=0.9, sharia_status="HARAM")
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_mixed_does_not_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(decision="BUY", confidence=0.9, sharia_status="MIXED")
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_vetoed_signal_does_not_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch)
    d = _debate(vetoed=True)
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_alerts_paused_suppresses_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch, alerts_paused=True)
    d = _debate(decision="BUY", confidence=0.9, sharia_status="HALAL")
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0


def test_dedup_suppresses_push(monkeypatch):
    captured, orch = _patch_push_deps(monkeypatch, dedup_match=True)
    d = _debate(decision="BUY", confidence=0.9, sharia_status="HALAL")
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 0
    assert captured["mark_sent_calls"] == []


# ---------------------------------------------------------------- robustness

def test_send_failure_does_not_mark_sent(monkeypatch):
    """If Telegram returns None (failed), we must NOT mark the row sent."""
    captured, orch = _patch_push_deps(monkeypatch, send_returns=None)
    d = _debate(decision="BUY", confidence=0.9, sharia_status="HALAL")
    _run(orch._maybe_push_signal_async(d, sid=1, btc_price=None))
    assert captured["send_calls"] == 1
    assert captured["mark_sent_calls"] == []


def test_send_exception_does_not_propagate(monkeypatch):
    """An exception in send_text must never break the scan loop."""
    from core import orchestrator
    from db.repos import runtime_config, signals as signals_repo
    from telegram_bot import bot as botmod

    async def boom(*_a, **_kw):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(botmod, "send_text", boom)
    monkeypatch.setattr(signals_repo, "should_dedup", lambda *a, **k: False)
    monkeypatch.setattr(signals_repo, "mark_sent", lambda *a, **k: None)
    monkeypatch.setattr(runtime_config, "get_value", lambda k, d=None: d)

    d = _debate(decision="BUY", confidence=0.9, sharia_status="HALAL")
    # Must not raise
    _run(orchestrator._maybe_push_signal_async(d, sid=1, btc_price=None))
