"""/buy /sell /positions — manual position tracking for compliance monitoring."""
from __future__ import annotations

import re

from db.repos import positions as positions_repo
from db.repos import stocks as stocks_repo
from telegram_bot.alerts import ARABIC
from telegram_bot import confirm

# Format examples (case-insensitive, flexible whitespace):
#   /buy TSLA @ 245.50 x 10
#   /buy TSLA 245.50 10
#   /buy TSLA@245.50×10
_BUY_RE = re.compile(
    r"""^\s*([A-Z\.]{1,8})        # symbol
        (?:\s*[@x×*]\s*|\s+)
        ([0-9]+(?:\.[0-9]+)?)      # price
        (?:\s*[x×*]\s*|\s+)
        ([0-9]+(?:\.[0-9]+)?)      # qty
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


async def buy(update, context):
    raw = " ".join(context.args).upper().strip()
    m = _BUY_RE.match(raw)
    if not m:
        await update.message.reply_text(
            "Usage: /buy SYMBOL @ PRICE × QTY\n"
            "Example: /buy TSLA @ 245.50 × 10"
        )
        return
    sym, price_s, qty_s = m.groups()
    try:
        price = float(price_s)
        qty = float(qty_s)
    except ValueError:
        await update.message.reply_text("Could not parse price or quantity.")
        return

    stock = stocks_repo.get(sym)
    sharia_status = stock.sharia_status if stock else "PENDING"
    label = ARABIC.get(sharia_status, sharia_status)
    if sharia_status == "HARAM":
        await update.message.reply_text(
            f"⚠️ {sym} is currently {label}. I will record the position but "
            "flag it for compliance review on every daily/weekly scan."
        )

    pid = positions_repo.open_position(
        symbol=sym, entry_price=price, quantity=qty,
        sharia_status_at_entry=sharia_status,
        notes="Recorded via /buy",
    )
    await update.message.reply_text(
        f"📥 Position opened: *{sym}* @ ${price:.2f} × {qty:g}\n"
        f"Sharia at entry: {label}\nID: {pid}",
        parse_mode="Markdown",
    )


async def sell(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /sell SYMBOL")
        return
    sym = context.args[0].upper()
    open_for = positions_repo.list_for_symbol(sym)
    open_only = [p for p in open_for if p.get("status") == "OPEN"]
    if not open_only:
        await update.message.reply_text(f"No OPEN position for {sym}.")
        return

    description = f"Close {len(open_only)} open position(s) for {sym}"

    async def _do():
        n = positions_repo.close_all_for_symbol(sym)
        return f"✅ Closed {n} {sym} position(s)."

    action_id, _ = confirm.register(description=description, callback=_do)
    keyboard = confirm.build_keyboard(action_id)
    await update.message.reply_text(
        f"❓ {description}? (60s timeout)",
        reply_markup=keyboard,
    )


async def positions_cmd(update, context):
    rows = positions_repo.list_open()
    if not rows:
        await update.message.reply_text("No open positions tracked.")
        return
    lines = ["*Tracked positions*"]
    for p in rows:
        sym = p.get("symbol")
        price = p.get("entry_price")
        qty = p.get("quantity")
        date = (p.get("entry_date") or "")[:10]
        sharia = ARABIC.get(p.get("sharia_status_at_entry") or "", "")
        lines.append(f"  • {sym}  ${price:.2f} × {qty:g}  ({date})  {sharia}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
