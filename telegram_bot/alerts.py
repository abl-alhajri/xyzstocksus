"""Auto-alert formatter — turns a DebateResult into a Telegram message.

Mixes English (technical content) with Arabic (Sharia status labels) per the
project plan's communication rules.
"""
from __future__ import annotations

from datetime import datetime
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
    """Render a BUY/HOLD signal in the format from the project spec.

    Output is intended for parse_mode='Markdown'. Stops/TPs/entry come from
    the synthesizer's structured payload.
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
    debt_ratio = cash_ratio = imp_ratio = None
    last_verified = None
    if sharia_out:
        s_in = (sharia_out.structured.get("structured") or {})
        sharia_status = s_in.get("status") or sharia_out.decision or "PENDING"
        drift = bool(s_in.get("drift_warning"))
        last_verified = s_in.get("as_of_filing")
    # Pull ratios from agent_input (we serialise them under round1's sharia.structured)
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
    lines.append(f"{icon} *NEW SIGNAL — {d.symbol}*")
    lines.append("")
    lines.append(f"🕌 *Sharia Status:* {sharia_label}")
    if last_verified:
        lines.append(f"   • Last verified: {last_verified}")
    if drift:
        lines.append("   • ⚠️ Drift warning — approaching breach")
    lines.append("")
    lines.append(f"📊 *Trade Type:* {trade_type}")
    lines.append(f"🎯 *Confidence:* {confidence_pct}%")
    lines.append("")

    if entry:
        lines.append(f"💵 *Entry:* {_fmt_zone(entry)}")
    if stop is not None:
        lines.append(f"🛡️ *Stop Loss:* {_fmt_num(stop)}")
    if tps:
        lines.append("")
        lines.append("✨ *Take Profits:*")
        for tp in tps:
            label = tp.get("label", "TP")
            price = _fmt_num(tp.get("price"))
            size = tp.get("size_pct")
            size_s = f" — sell {int(size)}%" if size else ""
            lines.append(f"   {label}: {price}{size_s}")
    lines.append("")
    lines.append(f"⚖️ *R:R:* {rr}")
    lines.append("")

    if btc_price is not None:
        lines.append(f"₿ *BTC:* ${_fmt_num(btc_price)} ({d.round1[0].structured.get('btc_regime') if d.round1 else ''})")
    if macro_text:
        lines.append(f"🎤 *Macro:* {macro_text}")
    if devil_text:
        lines.append(f"😈 *Devil's Advocate:* {devil_text}")

    lines.append("")
    summary = structured_inner.get("summary") or final.rationale
    if summary:
        lines.append("🧠 *Synthesis:*")
        lines.append(summary[:400])

    now_utc = datetime.now(tz=UTC)
    now_dubai = now_utc.astimezone(DUBAI)
    lines.append("")
    lines.append(f"⏰ Generated: {now_utc.strftime('%Y-%m-%d %H:%M UTC')} ({now_dubai.strftime('%I:%M %p Dubai')})")
    return "\n".join(lines)


def _render_vetoed(d: DebateResult) -> str:
    return (
        f"🚫 *SIGNAL REJECTED — {d.symbol}*\n\n"
        f"🕌 Sharia veto: {d.veto_reason or 'Sharia status not compliant'}\n"
        f"_No alert was emitted (signal would conflict with AAOIFI rules)._"
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
