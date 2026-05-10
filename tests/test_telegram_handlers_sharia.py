"""HTML migration regression tests for /sharia and /compliance handlers.

Specifically pins the XYZ-trigger case: notes containing snake_case
identifiers (market_cap, impermissible_revenue) which broke Markdown V1
with unbalanced underscores.
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


def _seed_xyz_ratios_with_snake_case_notes():
    """Reproduce the row that triggered the 400 on /sharia XYZ in production."""
    from db.repos import sharia as sharia_repo
    sharia_repo.insert_ratios(
        symbol="XYZ", market_cap=None, total_debt=None,
        interest_bearing_debt=None, cash_and_securities=None,
        total_revenue=None, impermissible_revenue=None,
        debt_ratio=None, cash_ratio=None, impermissible_ratio=None,
        sharia_status="MIXED", risk_tier="YELLOW",
        filing_date="2026-05-07", filing_type="10-Q",
        notes=("INCOMPLETE: missing market_cap (SEC shares × Tiingo close "
               "unavailable) | (extracted from SEC company_facts; "
               "impermissible_revenue=0 unless overridden)"),
    )


def test_sharia_cmd_renders_html_with_snake_case_notes():
    from telegram_bot.handlers import sharia as sharia_h
    _seed_xyz_ratios_with_snake_case_notes()

    update, sent, ctx = _make_update(args=["XYZ"])
    asyncio.run(sharia_h.sharia_cmd(update, ctx))

    assert len(sent) == 1
    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    body = msg["text"]
    # No Markdown bold delimiter around the title.
    assert "*Sharia" not in body
    # snake_case identifiers from verifier notes are present (proving the
    # actual offending substring made it through) but are now plain text
    # under HTML mode — not parser-fatal.
    assert "impermissible_revenue" in body
    assert "market_cap" in body
    # Bold tag landed.
    assert "<b>Sharia report — XYZ</b>" in body


def test_sharia_cmd_escapes_html_chars_in_dynamic_strings():
    """Symbol/notes containing <, >, & must be escaped, not parsed as HTML."""
    from telegram_bot.handlers import sharia as sharia_h
    from db.repos import sharia as sharia_repo, stocks as stocks_repo
    stocks_repo.set_sharia_status("AAPL", "HALAL")
    sharia_repo.insert_ratios(
        symbol="AAPL", market_cap=1e12, total_debt=1e10,
        interest_bearing_debt=1e10, cash_and_securities=5e10,
        total_revenue=4e11, impermissible_revenue=0.0,
        debt_ratio=0.01, cash_ratio=0.05, impermissible_ratio=0.0,
        sharia_status="HALAL", risk_tier="GREEN",
        filing_date="2025-09-30", filing_type="10-Q",
        notes="raw <b>injected</b> & special char",
    )
    update, sent, ctx = _make_update(args=["AAPL"])
    asyncio.run(sharia_h.sharia_cmd(update, ctx))

    body = sent[0]["text"]
    assert "&lt;b&gt;injected&lt;/b&gt;" in body
    assert "&amp;" in body
    # The literal injected <b> tag is gone — escape worked.
    assert "raw <b>injected</b>" not in body


def test_compliance_cmd_uses_html_render():
    from telegram_bot.handlers import sharia as sharia_h
    from db.repos import stocks as stocks_repo
    stocks_repo.set_sharia_status("AAPL", "HALAL")

    update, sent, ctx = _make_update(args=[])
    asyncio.run(sharia_h.compliance_cmd(update, ctx))

    msg = sent[0]
    assert msg["parse_mode"] == "HTML"
    assert "<b>Weekly Sharia Compliance Report</b>" in msg["text"]


def test_safe_html_reply_falls_back_to_plain_on_badrequest():
    """If HTML send raises BadRequest, the helper retries with parse_mode=None."""
    from telegram_bot.safe_reply import safe_html_reply
    from telegram.error import BadRequest

    sent: list[dict] = []
    calls = {"n": 0}

    class FakeMsg:
        async def reply_text(self, text, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BadRequest("Can't parse entities: oops")
            sent.append({"text": text, **kwargs})

    update = SimpleNamespace(message=FakeMsg())
    asyncio.run(safe_html_reply(update, "<b>Test</b>"))

    assert calls["n"] == 2  # one HTML attempt + one plain-text retry
    assert len(sent) == 1
    assert sent[0].get("parse_mode") is None
    assert sent[0]["text"] == "<b>Test</b>"
