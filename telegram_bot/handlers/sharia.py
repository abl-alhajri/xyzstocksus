"""/sharia /compliance — Sharia status reports.

Both handlers emit HTML (parse_mode='HTML'). Every dynamic substring —
ticker, status label, ratio %, notes, alert types/values — passes through
html.escape, so an unbalanced `_`/`*`/`[`/`<` from verifier notes (e.g.
snake_case identifiers like `impermissible_revenue`) can never break the
parser. Markdown V1 has no escape sequence; this is the same root cause
as the signal-alert 400s fixed in 4469ba7.
"""
from __future__ import annotations

from html import escape as h

from db.repos import sharia as sharia_repo
from db.repos.stocks import get
from sharia.reporter import build_weekly_report, render_html
from telegram_bot.alerts import ARABIC
from telegram_bot.safe_reply import safe_html_reply

# Telegram message hard cap is 4096; leave headroom for HTML tags.
_MAX_LEN = 3500
_TRUNCATE_SUFFIX = "\n…(truncated)"


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
    lines: list[str] = [f"<b>Sharia report — {h(sym)}</b>", "",
                        f"Status: {h(label)}"]
    if latest:
        filing_date = latest.get("filing_date") or "—"
        filing_type = latest.get("filing_type") or "—"
        lines.append(f"As of filing: {h(str(filing_date))}  "
                     f"(type {h(str(filing_type))})")
        risk_tier = latest.get("risk_tier") or "—"
        lines.append("")
        lines.append(f"• Debt / market cap:        {_pct(latest.get('debt_ratio'))} (max 30%)")
        lines.append(f"• Cash+securities / mcap:   {_pct(latest.get('cash_ratio'))} (max 30%)")
        lines.append(f"• Impermissible income:     {_pct(latest.get('impermissible_ratio'))} (max 5%)")
        lines.append(f"• Worst-tier:               {h(str(risk_tier))}")
        notes = latest.get("notes") or ""
        if notes:
            lines.append(f"• Notes: {h(str(notes)[:300])}")
    else:
        lines.append("No financial ratios yet — awaiting first verification.")

    alerts = sharia_repo.alerts_for_symbol(sym, limit=5)
    if alerts:
        lines.append("")
        lines.append("<b>Recent alerts:</b>")
        for a in alerts:
            sent_at = (a.get("sent_at") or "")[:10]
            atype = a.get("alert_type") or "?"
            old = a.get("old_value") or "—"
            new = a.get("new_value") or "—"
            lines.append(f"  • {h(sent_at)}  {h(str(atype))}: "
                         f"{h(str(old))} → {h(str(new))}")

    await safe_html_reply(update, "\n".join(lines))


async def compliance_cmd(update, context):
    rep = build_weekly_report(days=7)
    body = render_html(rep)
    if len(body) > _MAX_LEN:
        body = body[:_MAX_LEN] + _TRUNCATE_SUFFIX
    await safe_html_reply(update, body)


def _pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "—"
