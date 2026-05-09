"""Auto-alert formatter — turns a DebateResult into a Telegram message.

Mixes English (technical content) with Arabic (Sharia status labels) per the
project plan's communication rules.

Signal alerts (`render_signal`, `_render_vetoed`) emit HTML for
parse_mode='HTML'. Every dynamic substring — anything from the LLM, a
date, a label, or a ticker — passes through `html.escape`, so an unbalanced
`_`, `*`, `[`, `<`, etc. in LLM output can never break the parser. (Markdown
V1 has no escape sequence; this is the root cause of the 400 errors we hit.)

`render_status` and `render_compliance_alert` are still Markdown — their
callers (basic.status, sharia/monitor) live outside the scope of this fix
and will be migrated separately.
"""
from __future__ import annotations

from datetime import datetime
from html import escape as h
from zoneinfo import ZoneInfo

from agents.debate import DebateResult

DUBAI = ZoneInfo("Asia/Dubai")
UTC = ZoneInfo("UTC")

ARABIC = {
    "HALAL": "🟢 HALAL (شرعي)",
    "MIXED": "🟡 MIXED (مختلط)",
    "HARAM": "🔴 HARAM (غير شرعي)",
    "PENDING": "⚪ PENDING (معلّق)",
}


def render_signal(d: DebateResult, *, btc_price: float | None = None,
                  macro_quote: str | None = None) -> str:
    """Render a BUY/HOLD signal as HTML for parse_mode='HTML'.

    Stops/TPs/entry come from the synthesizer's structured payload.
    """
    final = d.final
    if d.vetoed or final is None:
        return _render_vetoed(d)

    structured_inner = (final.structured.get("structured") or {})
    confidence_pct = int(round(float(final.confidence or 0.0) * 100))
    decision = (final.decision or "HOLD").upper()
    trade_type = (final.structured.get("trade_type") or "—")

    # Sharia
    sharia_out = next((o for o in d.round1 if o.agent_name == "sharia"), None)
    sharia_status = "PENDING"
    drift = False
    last_verified = None
    if sharia_out:
        s_in = (sharia_out.structured.get("structured") or {})
        sharia_status = s_in.get("status") or sharia_out.decision or "PENDING"
        drift = bool(s_in.get("drift_warning"))
        last_verified = s_in.get("as_of_filing")
    sharia_label = ARABIC.get(sharia_status, sharia_status)

    # Devil's Advocate
    da_out = next((o for o in d.round1 if o.agent_name == "devils_advocate"), None)
    devil_text = (da_out.structured.get("structured") or {}).get("kill_thesis") if da_out else None
    if not devil_text and da_out:
        devil_text = da_out.rationale[:140] if da_out.rationale else None

    # Macro
    mv_out = next((o for o in d.round1 if o.agent_name == "macro_voice"), None)
    macro_text = (mv_out.rationale[:140] if mv_out else None) or (macro_quote or "")

    # Numerics
    entry = structured_inner.get("entry_zone")
    stop = structured_inner.get("stop_loss")
    tps = structured_inner.get("take_profits") or []
    rr = structured_inner.get("risk_reward") or "—"

    icon = "🟢" if decision == "BUY" else ("🟡" if decision == "HOLD" else "⚪")

    lines: list[str] = []
    lines.append(f"{icon} <b>NEW SIGNAL — {h(d.symbol)}</b>")
    lines.append("")
    lines.append(f"🕌 <b>Sharia Status:</b> {h(sharia_label)}")
    if last_verified:
        lines.append(f"   • Last verified: {h(str(last_verified))}")
    if drift:
        lines.append("   • ⚠️ Drift warning — approaching breach")
    lines.append("")
    lines.append(f"📊 <b>Trade Type:</b> {h(str(trade_type))}")
    lines.append(f"🎯 <b>Confidence:</b> {confidence_pct}%")
    lines.append("")

    if entry:
        lines.append(f"💵 <b>Entry:</b> {h(_fmt_zone(entry))}")
    if stop is not None:
        lines.append(f"🛡️ <b>Stop Loss:</b> ${_fmt_num(stop)}")
    if tps:
        lines.append("")
        lines.append("✨ <b>Take Profits:</b>")
        for tp in tps:
            label = tp.get("label", "TP")
            price = _fmt_num(tp.get("price"))
            size = tp.get("size_pct")
            size_s = f" — sell {int(size)}%" if size else ""
            lines.append(f"   {h(str(label))}: ${price}{size_s}")
    lines.append("")
    lines.append(f"⚖️ <b>R:R:</b> {h(str(rr))}")
    lines.append("")

    if btc_price is not None:
        regime = ""
        if d.round1:
            regime = str(d.round1[0].structured.get("btc_regime") or "")
        lines.append(f"₿ <b>BTC:</b> ${_fmt_num(btc_price)} ({h(regime)})")
    if macro_text:
        lines.append(f"🎤 <b>Macro:</b> {h(str(macro_text))}")
    if devil_text:
        lines.append(f"😈 <b>Devil's Advocate:</b> {h(str(devil_text))}")

    lines.append("")
    summary = structured_inner.get("summary") or final.rationale
    if summary:
        lines.append("🧠 <b>Synthesis:</b>")
        lines.append(h(str(summary)[:400]))

    now_utc = datetime.now(tz=UTC)
    now_dubai = now_utc.astimezone(DUBAI)
    lines.append("")
    lines.append(f"⏰ Generated: {now_utc.strftime('%Y-%m-%d %H:%M UTC')} ({now_dubai.strftime('%I:%M %p Dubai')})")
    return "\n".join(lines)


def _render_vetoed(d: DebateResult) -> str:
    return (
        f"🚫 <b>SIGNAL REJECTED — {h(d.symbol)}</b>\n\n"
        f"🕌 Sharia veto: {h(str(d.veto_reason or 'Sharia status not compliant'))}\n"
        f"<i>No alert was emitted (signal would conflict with AAOIFI rules).</i>"
    )


def _fmt_zone(zone) -> str:
    try:
        return f"${_fmt_num(zone[0])}-{_fmt_num(zone[1])}"
    except Exception:
        return str(zone)


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
        if n >= 1000:
            return f"{n:,.2f}"
        return f"{n:.2f}"
    except Exception:
        return str(v)


def render_status(report) -> str:
    """Compose a /status response. `report` carries the latest scan summary."""
    return (
        "*XYZStocksUS status*\n"
        f"• Last scan: {report.get('finished_at', '—')}\n"
        f"• Market: {report.get('market_status', '—')}\n"
        f"• BTC: ${report.get('btc_price', '—')} ({report.get('btc_regime', '—')})\n"
        f"• Candidates: {report.get('candidates_pool', 0)} → "
        f"prescreen {report.get('prescreen_pool', 0)} → "
        f"deep {report.get('deep_survivors', 0)}\n"
        f"• Today spend: ${report.get('today_usd', 0):.2f} / "
        f"month ${report.get('month_usd', 0):.2f}\n"
        f"• Quick-only mode: {report.get('quick_only', False)}\n"
    )


def render_compliance_alert(symbol: str, alert_type: str, old: str, new: str) -> str:
    icon = {"TIER_CHANGE": "📊", "STATUS_CHANGE": "🚨",
            "DRIFT_WARN": "⚠️", "NEW_FILING": "📄"}.get(alert_type, "ℹ️")
    title = {"TIER_CHANGE": "Tier change",
             "STATUS_CHANGE": "Sharia status change",
             "DRIFT_WARN": "Sharia Drift Radar",
             "NEW_FILING": "New SEC filing"}.get(alert_type, alert_type)
    return (
        f"{icon} *{title} — {symbol}*\n"
        f"  {old} → {new}"
    )
