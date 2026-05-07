"""/btc /macro — quick context lookups."""
from __future__ import annotations

from data.btc_feed import classify_regime, fetch_spot
from data.macro_feed import recent_quotes


async def btc_cmd(update, context):
    snap = fetch_spot(use_cache=True)
    regime = classify_regime()
    if snap is None:
        await update.message.reply_text("BTC price unavailable right now.")
        return
    last_close = regime.last_close or "—"
    sma20 = f"{regime.sma_20:,.0f}" if regime.sma_20 is not None else "—"
    sma50 = f"{regime.sma_50:,.0f}" if regime.sma_50 is not None else "—"
    text = (
        f"₿ *BTC:* ${snap.price:,.2f}\n"
        f"Regime: *{regime.label}*\n"
        f"SMA20 / SMA50: {sma20} / {sma50}\n"
        f"Last close: {last_close}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def macro_cmd(update, context):
    quotes = recent_quotes(limit=8)
    if not quotes:
        await update.message.reply_text("No macro quotes cached yet — refreshing...")
        try:
            from data.macro_feed import fetch_fed_speeches, fetch_fed_press
            fetch_fed_speeches()
            fetch_fed_press()
            quotes = recent_quotes(limit=8)
        except Exception:
            pass
    if not quotes:
        await update.message.reply_text("Macro feed unavailable.")
        return
    lines = ["*Recent macro quotes*\n"]
    for q in quotes:
        sentiment = q.get("sentiment") or "—"
        speaker = q.get("speaker") or "—"
        date = (q.get("date") or "")[:10]
        text = (q.get("quote_text") or "")[:200]
        icon = {"HAWKISH": "🦅", "DOVISH": "🕊️"}.get(sentiment, "•")
        lines.append(f"{icon} *{speaker}* ({date}) {sentiment}\n  {text}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
