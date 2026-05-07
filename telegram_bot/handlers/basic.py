"""/start, /help, /status — the always-available handlers."""
from __future__ import annotations

from datetime import datetime, timezone

from core import budget_guard
from core.logger import get_logger
from db.repos import runtime_config, signals as signals_repo
from telegram_bot.alerts import render_status

log = get_logger("telegram.basic")


HELP_TEXT = (
    "*XYZStocksUS — Telegram commands*\n\n"
    "*Status / info*\n"
    "/start /help /status — this menu / bot status\n"
    "/watch — watchlist with heuristic + Sharia\n"
    "/btc — BTC price + regime\n"
    "/macro — recent Powell/Fed/Trump quotes\n\n"
    "*Analysis*\n"
    "/analyze SYMBOL — full multi-agent debate (R1+R2+R3)\n"
    "/quick SYMBOL — faster analysis (R1+R3)\n"
    "/agents SYMBOL — last analysis broken down per agent\n"
    "/signals — last 10 signals\n\n"
    "*Sharia*\n"
    "/sharia SYMBOL — full Sharia status report\n"
    "/compliance — weekly compliance summary\n\n"
    "*Positions*\n"
    "/buy SYMBOL @ PRICE × QTY — record a position\n"
    "/sell SYMBOL — close a position\n"
    "/positions — show tracked positions\n\n"
    "*Admin*\n"
    "/scan — trigger manual scan\n"
    "/cost — API spend (today + month)\n"
    "/pause /resume — alerts on/off\n"
    "/disable SYMBOL /enable SYMBOL — toggle (with confirm)\n"
    "/threshold N — set min confidence (with confirm)\n"
)


async def start(update, context):
    log.info("/start", extra={"chat_id": update.effective_chat.id if update.effective_chat else None})
    await update.message.reply_text(
        "Welcome to XYZStocksUS — multi-agent stock signals with Sharia compliance.\n"
        "Type /help to see all commands.",
    )
    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/start", args=None, success=True,
    )


async def help_cmd(update, context):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/help", args=None, success=True,
    )


async def status(update, context):
    last = signals_repo.recent(1)
    last_ts = last[0]["timestamp"] if last else "—"
    bs = budget_guard.state()
    payload = {
        "finished_at": last_ts,
        "market_status": "—",
        "btc_price": "—",
        "btc_regime": "—",
        "candidates_pool": 0,
        "prescreen_pool": 0,
        "deep_survivors": 0,
        "today_usd": bs.today_usd,
        "month_usd": bs.month_usd,
        "quick_only": bs.quick_only,
    }
    # Fill BTC + market opportunistically
    try:
        from data.btc_feed import fetch_spot
        snap = fetch_spot(use_cache=True)
        if snap:
            payload["btc_price"] = f"{snap.price:,.0f}"
    except Exception:
        pass
    try:
        from core.market_calendar import status as mkt_status
        s = mkt_status()
        payload["market_status"] = s.label
    except Exception:
        pass

    await update.message.reply_text(render_status(payload), parse_mode="Markdown")
    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/status", args=None, success=True,
    )
