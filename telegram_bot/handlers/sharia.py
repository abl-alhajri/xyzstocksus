"""/sharia /compliance — Sharia status reports."""
from __future__ import annotations

from db.repos import sharia as sharia_repo
from db.repos.stocks import get
from sharia.reporter import build_weekly_report, render_markdown
from telegram_bot.alerts import ARABIC


async def sharia_cmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /sharia SYMBOL")
        return
    sym = context.args[0].upper()
    stock = get(sym)
    if not stock:
        await update.message.reply_text(f"{sym} is not in the watchlist.")
        return
    latest = sharia_repo.latest_ratios(sym)
    label = ARABIC.get(stock.sharia_status, stock.sharia_status)
    lines = [f"*Sharia report — {sym}*\n", f"Status: {label}"]
    if latest:
        lines.append(f"As of filing: {latest.get('filing_date') or '—'}  "
                     f"(type {latest.get('filing_type') or '—'})")
        debt = latest.get("debt_ratio")
        cash = latest.get("cash_ratio")
        imp = latest.get("impermissible_ratio")
        risk_tier = latest.get("risk_tier") or "—"
        lines.append("")
        lines.append(f"• Debt / market cap:        {_pct(debt)} (max 30%)")
        lines.append(f"• Cash+securities / mcap:   {_pct(cash)} (max 30%)")
        lines.append(f"• Impermissible income:     {_pct(imp)} (max 5%)")
        lines.append(f"• Worst-tier:               {risk_tier}")
        notes = latest.get("notes") or ""
        if notes:
            lines.append(f"• Notes: {notes[:300]}")
    else:
        lines.append("No financial ratios yet — awaiting first verification.")

    alerts = sharia_repo.alerts_for_symbol(sym, limit=5)
    if alerts:
        lines.append("\n*Recent alerts:*")
        for a in alerts:
            lines.append(f"  • {a.get('sent_at','')[:10]}  {a.get('alert_type')}: "
                        f"{a.get('old_value')} → {a.get('new_value')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def compliance_cmd(update, context):
    rep = build_weekly_report(days=7)
    md = render_markdown(rep)
    if len(md) > 3500:
        md = md[:3500] + "\n…"
    await update.message.reply_text(md, parse_mode="Markdown")


def _pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "—"
