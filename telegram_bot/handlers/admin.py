"""/scan /pause /resume /cost /threshold /disable /enable /refresh_sharia — admin commands."""
from __future__ import annotations

import asyncio
from html import escape as h

from config.settings import settings
from core import budget_guard, cost_tracker
from core.logger import get_logger
from db.repos import runtime_config
from db.repos import stocks as stocks_repo
from telegram_bot import confirm
from telegram_bot.safe_reply import safe_html_reply

log = get_logger("telegram.admin")


def _is_admin(update) -> bool:
    """True iff the message originates from settings.telegram_chat_id.

    /refresh_sharia is the first admin-gated command — runs a 3-6 min
    full-watchlist re-verification and incurs SEC + yfinance traffic, so we
    don't want strangers triggering it if the bot is ever shared.
    """
    if not settings.telegram_chat_id:
        return False
    chat = update.effective_chat
    if chat is None:
        return False
    return str(chat.id) == str(settings.telegram_chat_id)


async def scan_cmd(update, context):
    await update.message.reply_text("🔄 Manual scan started…")
    try:
        from core.orchestrator import run_scan
        report = run_scan()
        await update.message.reply_text(
            f"✅ Scan complete\n"
            f"  candidates: {report.candidates_pool}\n"
            f"  prescreen: {report.prescreen_pool}\n"
            f"  deep: {report.deep_survivors}\n"
            f"  signals: {len(report.signals_recorded)}\n"
            f"  notes: {' | '.join(report.notes) if report.notes else '—'}",
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Scan failed: {exc}")


async def pause_cmd(update, context):
    runtime_config.set_value("alerts_paused", True)
    await update.message.reply_text("⏸ Telegram alerts paused.")


async def resume_cmd(update, context):
    runtime_config.set_value("alerts_paused", False)
    budget_guard.disable_quick_only()
    await update.message.reply_text("▶️ Alerts resumed (and quick-only mode reset).")


async def cost_cmd(update, context):
    bs = budget_guard.state()
    breakdown = cost_tracker.per_agent_today()
    lines = [
        "<b>API spend</b>",
        f"  • Today: ${bs.today_usd:.2f} / soft ${2.50:.2f} / hard ${5.00:.2f}",
        f"  • Month: ${bs.month_usd:.2f} / hard $80.00",
        f"  • Deep today: {bs.deep_count_today} / 30",
        f"  • Quick-only mode: {'ON' if bs.quick_only else 'off'}",
        "",
        "<b>Per agent (today)</b>",
    ]
    if breakdown:
        for agent, total in sorted(breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  • {h(str(agent))}: ${total:.4f}")
    else:
        lines.append("  (no calls today)")
    await safe_html_reply(update, "\n".join(lines))


async def threshold_cmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /threshold N (0-1, e.g. 0.65)")
        return
    try:
        n = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid number.")
        return
    if not (0 <= n <= 1):
        await update.message.reply_text("Threshold must be in [0, 1].")
        return

    description = f"Set min-confidence-for-alert to {n:.2f}"

    async def _do():
        runtime_config.set_value("min_confidence_alert", n)
        return f"✅ Set min confidence to {n:.2f}"

    action_id, _ = confirm.register(description=description, callback=_do)
    kb = confirm.build_keyboard(action_id)
    await update.message.reply_text(f"❓ {description}? (60s timeout)", reply_markup=kb)


async def disable_cmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /disable SYMBOL")
        return
    sym = context.args[0].upper()
    stock = stocks_repo.get(sym)
    if not stock:
        await update.message.reply_text(f"{sym} is not in the watchlist.")
        return
    description = f"Disable {sym} (skip in scans)"

    async def _do():
        stocks_repo.set_enabled(sym, False)
        return f"✅ {sym} disabled."

    action_id, _ = confirm.register(description=description, callback=_do)
    kb = confirm.build_keyboard(action_id)
    await update.message.reply_text(f"❓ {description}? (60s timeout)", reply_markup=kb)


async def refresh_sharia_cmd(update, context):
    """Force-refresh Sharia status for every enabled ticker.

    Admin-only (chat_id must match settings.telegram_chat_id). The work runs
    on a background thread so the polling loop stays responsive; progress
    is sent back to the same chat as the verification advances.
    """
    if not _is_admin(update):
        await update.message.reply_text("⛔ Admin only.")
        log.info("refresh_sharia denied (non-admin)",
                 extra={"chat_id": update.effective_chat.id if update.effective_chat else None})
        return

    target_chat = str(update.effective_chat.id)
    await update.message.reply_text(
        "🔄 Sharia refresh started — full watchlist takes ~3-6 min."
    )

    loop = asyncio.get_running_loop()

    def progress(msg: str) -> None:
        # Called from the worker thread — schedule the async send back on
        # the polling loop so we don't touch PTB internals from off-thread.
        from telegram_bot.bot import send_text
        try:
            asyncio.run_coroutine_threadsafe(
                send_text(msg, chat_id=target_chat), loop,
            )
        except Exception as exc:
            log.warning("refresh progress send failed", extra={"err": str(exc)})

    def _work() -> None:
        from sharia.monitor import run_full_refresh
        try:
            run_full_refresh(progress_cb=progress, every=5)
        except Exception as exc:
            log.error("refresh_sharia worker crashed", extra={"err": str(exc)})
            progress(f"❌ Refresh crashed: {exc}")

    asyncio.create_task(asyncio.to_thread(_work))


async def enable_cmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /enable SYMBOL")
        return
    sym = context.args[0].upper()
    stock = stocks_repo.get(sym)
    if not stock:
        await update.message.reply_text(f"{sym} is not in the watchlist.")
        return
    description = f"Enable {sym} (include in scans)"

    async def _do():
        stocks_repo.set_enabled(sym, True)
        return f"✅ {sym} enabled."

    action_id, _ = confirm.register(description=description, callback=_do)
    kb = confirm.build_keyboard(action_id)
    await update.message.reply_text(f"❓ {description}? (60s timeout)", reply_markup=kb)
