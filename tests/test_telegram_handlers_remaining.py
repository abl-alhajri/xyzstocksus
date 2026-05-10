"""HTML migration regression tests for the remaining 5 handlers.

watchlist, /cost, /btc, /macro, /help, /status, /buy, /positions all moved
from Markdown V1 to HTML. The high-risk inputs (snake_case sectors, agent
names, free-form Fed quote text) are pinned here so a regression doesn't
silently re-introduce 400 Bad Request behaviour.
"""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

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


def _make_update(args):
    sent: list[dict] = []

    class FakeMsg:
        async def reply_text(self, text, **kwargs):
            sent.append({"text": text, **kwargs})

    update = SimpleNamespace(
        message=FakeMsg(),
        effective_chat=SimpleNamespace(id=1),
    )
    return update, sent, SimpleNamespace(args=args)


# -------------------------- /watch ----------------------------------------

def test_watch_renders_html_with_underscored_sectors():
    """Sector strings like BTC_TREASURY contain underscores that broke Markdown V1."""
    from telegram_bot.handlers import watchlist as watch_h

    update, sent, ctx = _make_update(args=[])
    asyncio.run(watch_h.watch(update, ctx))

    # First reply is the watchlist body (the migrate fixture seeded the
    # full watchlist, including BTC_TREASURY tickers).
    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    # Underscored sector header lands as plain text under HTML wrapping —
    # not parser-fatal, not silently italicised.
    assert "<b><i>BTC_TREASURY</i></b>" in body
    assert "<b>Watchlist</b>" in body
    # No leftover Markdown bold around the title.
    assert "*Watchlist*" not in body
    # Tickers wrapped in <b>, not *
    assert "<b>MSTR</b>" in body or "<b>MARA</b>" in body


# -------------------------- /cost -----------------------------------------

def test_cost_renders_html_with_snake_case_agent_names(monkeypatch):
    """Agent names like technical_analyst would have broken Markdown V1."""
    from telegram_bot.handlers import admin as admin_h
    from core import cost_tracker, budget_guard

    monkeypatch.setattr(
        cost_tracker, "per_agent_today",
        lambda: {"technical_analyst": 0.05,
                 "btc_macro_analyst": 0.03,
                 "devils_advocate": 0.01},
    )
    fake_state = SimpleNamespace(
        today_usd=0.09, month_usd=1.23, deep_count_today=2, quick_only=False,
    )
    monkeypatch.setattr(budget_guard, "state", lambda: fake_state)

    update, sent, ctx = _make_update(args=[])
    asyncio.run(admin_h.cost_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    # snake_case identifiers present as plain text — not parser-fatal.
    assert "technical_analyst" in body
    assert "btc_macro_analyst" in body
    assert "devils_advocate" in body
    # HTML bold for headers, no Markdown leakage.
    assert "<b>API spend</b>" in body
    assert "<b>Per agent (today)</b>" in body
    assert "*API spend*" not in body


# -------------------------- /btc + /macro ---------------------------------

def test_btc_renders_html(monkeypatch):
    from telegram_bot.handlers import macro as macro_h
    from data import btc_feed

    monkeypatch.setattr(
        btc_feed, "fetch_spot",
        lambda use_cache=True: SimpleNamespace(price=84500.0),
    )
    monkeypatch.setattr(
        btc_feed, "classify_regime",
        lambda: SimpleNamespace(label="BULL", sma_20=82000, sma_50=78000,
                                last_close=84200),
    )

    update, sent, ctx = _make_update(args=[])
    asyncio.run(macro_h.btc_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    assert "<b>BTC:</b>" in body
    assert "<b>BULL</b>" in body
    assert "*BTC:*" not in body


def test_macro_renders_html_and_escapes_freeform_quote_text(monkeypatch):
    """Quote text from Fed RSS could contain *, _, [ chars — must be escaped."""
    from telegram_bot.handlers import macro as macro_h

    quotes = [{
        "speaker": "Powell",
        "date": "2026-05-09",
        "sentiment": "HAWKISH",
        "quote_text": "*Powell* said _things_ about [rates] & <inflation>",
    }]
    # Patch the binding the handler uses (the `from … import` brought a local
    # reference, so patching data.macro_feed has no effect on the closure).
    monkeypatch.setattr(macro_h, "recent_quotes", lambda limit=8: quotes)

    update, sent, ctx = _make_update(args=[])
    asyncio.run(macro_h.macro_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    # Free-form Markdown-ish chars passed through as plain text.
    assert "*Powell* said _things_ about [rates]" in body
    # HTML-special chars escaped.
    assert "&amp;" in body
    assert "&lt;inflation&gt;" in body
    # Speaker wrapped in <b>, no Markdown leak.
    assert "<b>Powell</b>" in body
    # Header HTML.
    assert "<b>Recent macro quotes</b>" in body


# -------------------------- /help + /status -------------------------------

def test_help_renders_html():
    from telegram_bot.handlers import basic as basic_h

    update, sent, ctx = _make_update(args=[])
    asyncio.run(basic_h.help_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    assert "<b>XYZStocksUS — Telegram commands</b>" in body
    assert "<b>Sharia</b>" in body
    # No Markdown bold leaked.
    assert "*XYZStocksUS" not in body


def test_status_renders_html(monkeypatch):
    from telegram_bot.handlers import basic as basic_h
    from core import budget_guard

    fake_state = SimpleNamespace(
        today_usd=0.5, month_usd=12.3, quick_only=False, deep_count_today=0,
    )
    monkeypatch.setattr(budget_guard, "state", lambda: fake_state)

    update, sent, ctx = _make_update(args=[])
    asyncio.run(basic_h.status(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    assert "<b>XYZStocksUS status</b>" in body
    assert "*XYZStocksUS status*" not in body


# -------------------------- /buy + /positions -----------------------------

def test_buy_renders_html():
    from telegram_bot.handlers import positions as pos_h

    update, sent, ctx = _make_update(args=["TSLA", "@", "245.50", "×", "10"])
    asyncio.run(pos_h.buy(update, ctx))

    # Last sent message is the confirmation. There may be a HARAM warning
    # before it; we just assert no Markdown leaked into the HTML send.
    final = sent[-1]
    assert final["parse_mode"] == "HTML"
    body = final["text"]
    assert "<b>TSLA</b>" in body
    assert "*TSLA*" not in body


def test_positions_renders_html():
    from telegram_bot.handlers import positions as pos_h
    from db.repos import positions as positions_repo

    positions_repo.open_position(
        symbol="TSLA", entry_price=245.0, quantity=10,
        sharia_status_at_entry="HALAL",
    )
    update, sent, ctx = _make_update(args=[])
    asyncio.run(pos_h.positions_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    assert "<b>Tracked positions</b>" in body
    assert "TSLA" in body
    assert "*Tracked positions*" not in body


# -------------------------- alerts.render_status --------------------------

def test_render_status_emits_html():
    """render_status moved from Markdown to HTML — confirm new format."""
    from telegram_bot.alerts import render_status

    body = render_status({
        "finished_at": "2026-05-10T14:00:00+00:00",
        "market_status": "OPEN",
        "btc_price": "84,500",
        "btc_regime": "BULL",
        "candidates_pool": 50,
        "prescreen_pool": 15,
        "deep_survivors": 3,
        "today_usd": 0.45,
        "month_usd": 12.30,
        "quick_only": False,
    })
    assert "<b>XYZStocksUS status</b>" in body
    assert "*XYZStocksUS" not in body
