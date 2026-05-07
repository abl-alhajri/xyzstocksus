"""Commit 14 tests — confirm flow + position parser + dedup of expired actions."""
from __future__ import annotations

import asyncio
import importlib
import re
import time

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


# ---------------------- /buy parser ---------------------------------------

def test_buy_regex_at_x_separator():
    from telegram_bot.handlers.positions import _BUY_RE
    m = _BUY_RE.match("TSLA @ 245.50 x 10")
    assert m
    assert m.group(1) == "TSLA"
    assert m.group(2) == "245.50"
    assert m.group(3) == "10"


def test_buy_regex_compact():
    from telegram_bot.handlers.positions import _BUY_RE
    m = _BUY_RE.match("MSTR@350×3.5")
    assert m
    assert m.group(1) == "MSTR"
    assert m.group(2) == "350"
    assert m.group(3) == "3.5"


def test_buy_regex_rejects_garbage():
    from telegram_bot.handlers.positions import _BUY_RE
    assert _BUY_RE.match("buy something") is None


# ---------------------- confirm flow --------------------------------------

def test_confirm_register_and_run():
    from telegram_bot import confirm
    out = {"called": False}

    async def cb():
        out["called"] = True
        return "done!"

    aid, label = confirm.register(description="test action", callback=cb)
    assert label == "✅ Confirm"
    assert aid in confirm._PENDING

    # Simulate the callback fire
    class FakeQuery:
        data = f"confirm:{aid}"
        async def answer(self): pass
    class FakeUpdate:
        callback_query = FakeQuery()

    res = asyncio.run(confirm.handle_callback(FakeUpdate(), None))
    assert out["called"] is True
    assert res == "done!"
    assert aid not in confirm._PENDING


def test_confirm_cancel():
    from telegram_bot import confirm

    async def cb():
        return "should not run"

    aid, _ = confirm.register(description="cancel me", callback=cb)

    class FakeQuery:
        data = f"cancel:{aid}"
        async def answer(self): pass
    class FakeUpdate:
        callback_query = FakeQuery()

    res = asyncio.run(confirm.handle_callback(FakeUpdate(), None))
    assert "Cancelled" in res
    assert aid not in confirm._PENDING


def test_confirm_expired_returns_message():
    from telegram_bot import confirm

    async def cb():
        return "x"

    aid, _ = confirm.register(description="age me", callback=cb)
    # Expire by mutating creation time
    confirm._PENDING[aid].created_at = time.time() - 200

    class FakeQuery:
        data = f"confirm:{aid}"
        async def answer(self): pass
    class FakeUpdate:
        callback_query = FakeQuery()

    res = asyncio.run(confirm.handle_callback(FakeUpdate(), None))
    assert "timed out" in res


def test_confirm_unknown_action():
    from telegram_bot import confirm

    class FakeQuery:
        data = "confirm:nonexistent"
        async def answer(self): pass
    class FakeUpdate:
        callback_query = FakeQuery()

    res = asyncio.run(confirm.handle_callback(FakeUpdate(), None))
    assert "expired" in res or "already handled" in res


# ---------------------- alerts dedup --------------------------------------

def test_dedup_wraps_repo_with_settings():
    from db.repos import signals as signals_repo
    from core import dedup
    sid = signals_repo.insert_signal(
        symbol="TSLA", decision="BUY", trade_type="SWING",
        confidence=0.70, sharia_status="HALAL", full_synthesis=None,
    )
    signals_repo.mark_sent(sid, telegram_msg_id=1)

    # Same confidence within window → suppress
    assert dedup.should_suppress("TSLA", new_confidence=0.70) is True
    # Big jump bypasses
    assert dedup.should_suppress("TSLA", new_confidence=0.85) is False
