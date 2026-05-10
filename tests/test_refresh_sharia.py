"""Tests for /refresh_sharia (admin handler) + sharia.monitor.run_full_refresh.

Coverage:
  - run_full_refresh: per-ticker error isolation, status-change tracking,
    cache fallback when both data sources fail, INCOMPLETE skip when no
    cache exists either, progress callback cadence.
  - admin handler: chat_id auth check (non-admin denied, admin proceeds).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------- monitor

def _patch_monitor(monkeypatch, *, syms, latest_ratios=None, verify_results=None,
                   verify_raises=None, info_returns=None, facts_returns=None):
    """Wire fakes into sharia.monitor for run_full_refresh.

    `latest_ratios`, `verify_results`, `verify_raises`, `info_returns`,
    `facts_returns` are dicts keyed by symbol.
    """
    from sharia import monitor as mon
    from db.repos import sharia as sharia_repo, stocks as stocks_repo

    latest_ratios = latest_ratios or {}
    verify_results = verify_results or {}
    verify_raises = verify_raises or {}
    info_returns = info_returns or {}
    facts_returns = facts_returns or {}

    def fake_list_all(enabled_only=True):
        return [SimpleNamespace(symbol=s) for s in syms]

    def fake_latest_ratios(sym):
        return latest_ratios.get(sym)

    def fake_verify(sym, **kw):
        if sym in verify_raises:
            raise verify_raises[sym]
        return verify_results.get(sym)

    def fake_info(sym):
        return info_returns.get(sym, {"marketCap": 1e12})

    def fake_facts(sym):
        return facts_returns.get(sym, {"facts": {}})

    def fake_mc(sym):
        return 1e12

    monkeypatch.setattr(stocks_repo, "list_all", fake_list_all)
    monkeypatch.setattr(sharia_repo, "latest_ratios", fake_latest_ratios)
    monkeypatch.setattr(mon, "verify", fake_verify)
    return mon, fake_info, fake_facts, fake_mc


def _vresult(status, debt_ratio=0.05):
    """Tiny VerificationResult fake just supplying the fields run_full_refresh reads."""
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        ratios=SimpleNamespace(debt_ratio=debt_ratio),
    )


def test_full_refresh_counts_statuses_and_changes(monkeypatch):
    mon, fi, ff, fmc = _patch_monitor(
        monkeypatch,
        syms=["AAPL", "MSFT", "TSLA"],
        latest_ratios={
            "AAPL": {"sharia_status": "HALAL", "debt_ratio": 0.28,
                     "fetched_at": "2026-04-01T00:00:00+00:00"},
            "MSFT": {"sharia_status": "HALAL", "debt_ratio": 0.05,
                     "fetched_at": "2026-04-01T00:00:00+00:00"},
            # TSLA: no previous row
        },
        verify_results={
            "AAPL": _vresult("MIXED", debt_ratio=0.32),  # status change
            "MSFT": _vresult("HALAL", debt_ratio=0.05),  # no change
            "TSLA": _vresult("HALAL", debt_ratio=0.10),  # first verify
        },
    )

    out = mon.run_full_refresh(progress_cb=None, every=99,
                               fetch_yfinance_info=fi,
                               fetch_company_facts=ff,
                               fetch_market_cap=fmc)

    assert out["total"] == 3
    assert out["by_status"]["HALAL"] == 2
    assert out["by_status"]["MIXED"] == 1
    assert len(out["status_changes"]) == 1
    assert out["status_changes"][0]["symbol"] == "AAPL"
    assert out["status_changes"][0]["old"] == "HALAL"
    assert out["status_changes"][0]["new"] == "MIXED"
    assert out["errors"] == []


def test_one_ticker_failing_does_not_break_the_rest(monkeypatch):
    mon, fi, ff, fmc = _patch_monitor(
        monkeypatch,
        syms=["AAPL", "BAD", "TSLA"],
        verify_results={
            "AAPL": _vresult("HALAL"),
            "TSLA": _vresult("HALAL"),
        },
        verify_raises={"BAD": RuntimeError("XBRL malformed")},
    )

    out = mon.run_full_refresh(progress_cb=None, every=99,
                               fetch_yfinance_info=fi,
                               fetch_company_facts=ff,
                               fetch_market_cap=fmc)

    assert out["total"] == 3
    assert out["by_status"]["HALAL"] == 2
    assert len(out["errors"]) == 1
    assert out["errors"][0]["symbol"] == "BAD"
    assert "XBRL" in out["errors"][0]["err"]


def test_cache_fallback_used_when_both_sources_fail(monkeypatch):
    """yfinance None + SEC None + previous row → use cache, count it, no error."""
    mon, _fi, _ff, fmc = _patch_monitor(
        monkeypatch,
        syms=["AAPL"],
        latest_ratios={
            "AAPL": {"sharia_status": "HALAL", "debt_ratio": 0.05,
                     "fetched_at": "2026-03-01T00:00:00+00:00"},
        },
        info_returns={"AAPL": None},
        facts_returns={"AAPL": None},
    )

    out = mon.run_full_refresh(
        progress_cb=None, every=99,
        fetch_yfinance_info=lambda s: None,
        fetch_company_facts=lambda s: None,
        fetch_market_cap=fmc,
    )

    assert out["used_cache"] == ["AAPL"]
    assert out["by_status"]["HALAL"] == 1
    assert out["errors"] == []


def test_no_data_no_cache_marked_incomplete(monkeypatch):
    """yfinance None + SEC None + no previous row → INCOMPLETE error, no persist."""
    mon, _fi, _ff, fmc = _patch_monitor(
        monkeypatch,
        syms=["NEWBIE"],
        latest_ratios={},
    )

    out = mon.run_full_refresh(
        progress_cb=None, every=99,
        fetch_yfinance_info=lambda s: None,
        fetch_company_facts=lambda s: None,
        fetch_market_cap=fmc,
    )

    assert out["used_cache"] == []
    assert len(out["errors"]) == 1
    assert "no data + no cache" in out["errors"][0]["reason"]


def test_etf_bypass_runs_before_data_fetches(monkeypatch):
    """HLAL/SPUS/SPSK must reach verify() even when yfinance + SEC both fail.

    Without the early bypass, info=None + facts=None hits the cached-row
    branch and continues, never calling verify(). This regression test pins
    the bypass to BEFORE the fetcher calls.
    """
    fetched: list[tuple[str, str]] = []

    mon, _fi, _ff, fmc = _patch_monitor(
        monkeypatch,
        syms=["HLAL"],
        latest_ratios={
            "HLAL": {"sharia_status": "PENDING", "debt_ratio": None,
                     "fetched_at": "2026-04-01T00:00:00+00:00"},
        },
        verify_results={"HLAL": _vresult("HALAL")},
    )

    def tracking_info(sym):
        fetched.append(("info", sym))
        return None

    def tracking_facts(sym):
        fetched.append(("facts", sym))
        return None

    out = mon.run_full_refresh(
        progress_cb=None, every=99,
        fetch_yfinance_info=tracking_info,
        fetch_company_facts=tracking_facts,
        fetch_market_cap=fmc,
    )

    # Bypass triggered: HLAL counted as HALAL, not cached, no errors.
    assert out["by_status"]["HALAL"] == 1
    assert out["used_cache"] == []
    assert out["errors"] == []
    # Status changed PENDING → HALAL via bypass.
    assert any(c["symbol"] == "HLAL" and c["new"] == "HALAL"
               for c in out["status_changes"])
    # Critical: fetchers were NOT called for HLAL — the bypass ran first.
    assert ("info", "HLAL") not in fetched
    assert ("facts", "HLAL") not in fetched


def test_progress_callback_called_at_cadence_and_summary(monkeypatch):
    mon, fi, ff, fmc = _patch_monitor(
        monkeypatch,
        syms=[f"S{i}" for i in range(12)],
        verify_results={f"S{i}": _vresult("HALAL") for i in range(12)},
    )

    msgs: list[str] = []
    mon.run_full_refresh(progress_cb=msgs.append, every=5,
                         fetch_yfinance_info=fi,
                         fetch_company_facts=ff,
                         fetch_market_cap=fmc)

    # start + ticks at 5,10 + final summary  = 4 messages
    # (no extra tick at i==total since the loop guards against that)
    assert len(msgs) == 4
    assert "Starting" in msgs[0]
    assert "Verified 5/12" in msgs[1]
    assert "Verified 10/12" in msgs[2]
    assert "Done" in msgs[3]


# --------------------------------------------------------------- admin handler

def _make_update(chat_id):
    sent: list[str] = []

    class FakeMsg:
        async def reply_text(self, text, **_kw):
            sent.append(text)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=FakeMsg(),
    )
    return update, sent


def test_refresh_sharia_denies_non_admin(monkeypatch):
    from telegram_bot.handlers import admin as admin_mod

    class FakeSettings:
        telegram_chat_id = "8588842240"

    monkeypatch.setattr(admin_mod, "settings", FakeSettings)

    update, sent = _make_update(chat_id=99999999)
    asyncio.run(admin_mod.refresh_sharia_cmd(update, context=None))

    assert sent == ["⛔ Admin only."]


def test_refresh_sharia_starts_for_admin(monkeypatch):
    """Admin ack is sent immediately; the worker is dispatched as an asyncio task.

    We don't await the task here — we only verify the auth gate passed and
    the ack was posted. Worker behaviour is covered by the run_full_refresh
    tests above.
    """
    from telegram_bot.handlers import admin as admin_mod

    class FakeSettings:
        telegram_chat_id = "8588842240"

    monkeypatch.setattr(admin_mod, "settings", FakeSettings)
    # Stub run_full_refresh so the background thread is harmless if it fires.
    import sharia.monitor as mon
    monkeypatch.setattr(mon, "run_full_refresh", lambda **_kw: {"total": 0})

    update, sent = _make_update(chat_id=8588842240)
    asyncio.run(admin_mod.refresh_sharia_cmd(update, context=None))

    assert len(sent) == 1
    assert "Sharia refresh started" in sent[0]
