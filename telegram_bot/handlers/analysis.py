"""/analyze /quick /agents /signals — on-demand multi-agent analysis."""
from __future__ import annotations

import asyncio
import json

from agents.base import AgentInput
from agents.debate import run_debate_async
from config.agent_sets import SETS_BY_NAME
from core.logger import get_logger
from db.repos import runtime_config, sharia as sharia_repo, signals as signals_repo
from db.repos.stocks import get
from telegram_bot.alerts import render_signal

log = get_logger("telegram.analysis")


def _parse_symbol(args) -> str | None:
    if not args:
        return None
    return str(args[0]).upper()


async def _build_input_for(symbol: str) -> AgentInput | None:
    stock = get(symbol)
    if not stock:
        return None

    # Best-effort data pull — if any layer fails we still return a usable
    # AgentInput (the agents handle missing fields gracefully).
    try:
        from data.btc_feed import fetch_spot, classify_regime
        from data.prices import fetch_history
        from indicators.technical import summarize as tech_summary
        from indicators.correlation import btc_correlation_30d

        snap = fetch_spot(use_cache=True)
        regime = classify_regime()

        res = fetch_history([symbol, "BTC-USD"], period="120d", interval="1d")
        df = res.frames.get(symbol)
        btc_df = res.frames.get("BTC-USD")
        tech = tech_summary(df)

        stock_closes = []
        if df is not None:
            try:
                stock_closes = [float(x) for x in df["Close"].tolist()]
            except Exception:
                pass
        btc_closes = []
        if btc_df is not None:
            try:
                btc_closes = [float(x) for x in btc_df["Close"].tolist()]
            except Exception:
                pass
        corr = btc_correlation_30d(stock_closes, btc_closes) if stock_closes and btc_closes else None
    except Exception as exc:
        log.warning("on-demand data fetch failed", extra={"err": str(exc)})
        snap = None
        regime = None
        tech = None
        corr = None

    sr = sharia_repo.latest_ratios(symbol)
    return AgentInput(
        symbol=stock.symbol,
        sector=stock.sector,
        agent_set=stock.agent_set,
        sharia_status=stock.sharia_status,
        last_price=getattr(tech, "last_close", None) if tech else None,
        heuristic={"total": 0, "notes": ["on-demand /analyze"]},
        technical=(tech.__dict__ if tech else None),
        btc_price=snap.price if snap else None,
        btc_regime=getattr(regime, "label", None),
        btc_corr_30d=corr,
        btc_beta=stock.btc_beta,
        sharia_ratios=sr,
    )


async def _run_and_reply(update, symbol: str, *, force_full: bool):
    inp = await _build_input_for(symbol)
    if inp is None:
        await update.message.reply_text(f"{symbol} is not in the watchlist.")
        return
    agent_set = SETS_BY_NAME.get(inp.agent_set, SETS_BY_NAME["standard"])
    result = await run_debate_async(inp, agent_set, force_full_mode=force_full)
    text = render_signal(result, btc_price=inp.btc_price)
    await update.message.reply_text(text, parse_mode="Markdown",
                                   disable_web_page_preview=True)


async def analyze(update, context):
    sym = _parse_symbol(context.args)
    if not sym:
        await update.message.reply_text("Usage: /analyze SYMBOL")
        return
    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/analyze", args=sym, success=True,
    )
    await update.message.reply_text(f"Analyzing {sym} (full debate)…")
    await _run_and_reply(update, sym, force_full=True)


async def quick(update, context):
    sym = _parse_symbol(context.args)
    if not sym:
        await update.message.reply_text("Usage: /quick SYMBOL")
        return
    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/quick", args=sym, success=True,
    )
    await update.message.reply_text(f"Quick analysis on {sym}…")
    await _run_and_reply(update, sym, force_full=False)


async def signals(update, context):
    rows = signals_repo.recent(10)
    if not rows:
        await update.message.reply_text("No signals yet.")
        return
    lines = ["*Recent signals (last 10)*"]
    for r in rows:
        decision = r.get("decision", "?")
        sym = r.get("symbol", "?")
        conf = r.get("confidence")
        ts = (r.get("timestamp") or "")[:19]
        sharia = r.get("sharia_status") or ""
        conf_s = f"{int(round((conf or 0) * 100))}%" if conf is not None else "—"
        lines.append(f"  • {ts}  {sym}  {decision}  {conf_s}  {sharia}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def agents(update, context):
    sym = _parse_symbol(context.args)
    if not sym:
        await update.message.reply_text("Usage: /agents SYMBOL")
        return
    rows = signals_repo.recent(50)
    target = next((r for r in rows if r.get("symbol", "").upper() == sym), None)
    if not target:
        await update.message.reply_text(f"No recent signal found for {sym}.")
        return
    outs = signals_repo.outputs_for_signal(target["id"])
    if not outs:
        await update.message.reply_text(f"No agent outputs stored for last {sym} signal.")
        return
    lines = [f"*Last debate breakdown — {sym}*\n"]
    for o in outs:
        decision = o.get("decision") or "?"
        conf = o.get("confidence") or 0.0
        agent = o.get("agent_name") or "?"
        rationale = ""
        try:
            structured = json.loads(o.get("output_json") or "{}")
            rationale = (structured.get("rationale")
                         or structured.get("structured", {}).get("kill_thesis")
                         or "")[:160]
        except Exception:
            pass
        lines.append(f"*{agent}* — {decision} ({int(round(conf*100))}%)\n  {rationale}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
