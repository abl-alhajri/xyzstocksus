"""/scan /pause /resume /cost /threshold /disable /enable — admin commands."""
from __future__ import annotations

from core import budget_guard, cost_tracker
from core.logger import get_logger
from db.repos import runtime_config
from db.repos import stocks as stocks_repo
from telegram_bot import confirm

log = get_logger("telegram.admin")


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
        "*API spend*",
        f"  • Today: ${bs.today_usd:.2f} / soft ${2.50:.2f} / hard ${5.00:.2f}",
        f"  • Month: ${bs.month_usd:.2f} / hard $80.00",
        f"  • Deep today: {bs.deep_count_today} / 30",
        f"  • Quick-only mode: {'ON' if bs.quick_only else 'off'}",
        "",
        "*Per agent (today)*",
    ]
    if breakdown:
        for agent, total in sorted(breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  • {agent}: ${total:.4f}")
    else:
        lines.append("  (no calls today)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
