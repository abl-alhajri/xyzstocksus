"""Weekly compliance report formatter.

Pulls the last week of compliance_alerts + current per-symbol status and
renders a Telegram-friendly HTML brief plus a JSON payload for the
dashboard's Sharia tab. HTML (not Markdown V1) so unbalanced `_`/`*`
chars in alert values can never break the Telegram parser.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape as h

from db.repos import sharia as sharia_repo
from db.repos import stocks as stocks_repo
from sharia.aaoifi import ShariaStatus

ARABIC_LABEL = {
    "HALAL": "🟢 شرعي",
    "MIXED": "🟡 مختلط",
    "HARAM": "🔴 غير شرعي",
    "PENDING": "⚪ معلّق",
}


@dataclass
class WeeklyReport:
    generated_at: str
    counts: dict[str, int]      # status → count
    tier_changes: list[dict]
    drift_warnings: list[dict]
    new_filings: list[dict]
    halal: list[str]
    mixed: list[str]
    haram: list[str]
    pending: list[str]


def build_weekly_report(*, days: int = 7) -> WeeklyReport:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    alerts = [a for a in sharia_repo.recent_alerts(limit=500)
              if a.get("sent_at", "") >= cutoff]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        grouped[a.get("alert_type") or "OTHER"].append(a)

    all_stocks = stocks_repo.list_all(enabled_only=False)
    by_status: dict[str, list[str]] = defaultdict(list)
    for s in all_stocks:
        by_status[s.sharia_status].append(s.symbol)

    counts = {k: len(v) for k, v in by_status.items()}

    return WeeklyReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        counts=counts,
        tier_changes=grouped.get("TIER_CHANGE", []),
        drift_warnings=grouped.get("DRIFT_WARN", []),
        new_filings=grouped.get("NEW_FILING", []),
        halal=sorted(by_status.get(ShariaStatus.HALAL.value, [])),
        mixed=sorted(by_status.get(ShariaStatus.MIXED.value, [])),
        haram=sorted(by_status.get(ShariaStatus.HARAM.value, [])),
        pending=sorted(by_status.get("PENDING", [])),
    )


def render_html(report: WeeklyReport) -> str:
    """Telegram-friendly HTML rendering of a WeeklyReport.

    Symbol lists and alert values pass through html.escape; the Arabic
    labels and emoji are safe HTML on their own.
    """
    lines: list[str] = []
    lines.append("📋 <b>Weekly Sharia Compliance Report</b>")
    lines.append(f"<i>Generated: {h(report.generated_at)}</i>")
    lines.append("")
    lines.append("<b>Status counts:</b>")
    for k in ("HALAL", "MIXED", "HARAM", "PENDING"):
        lines.append(f"  {ARABIC_LABEL.get(k, k)}: {report.counts.get(k, 0)}")
    lines.append("")

    if report.tier_changes:
        lines.append("<b>Tier changes (last 7d):</b>")
        for c in report.tier_changes:
            lines.append(
                f"  • {h(str(c['symbol']))}: "
                f"{h(str(c.get('old_value') or '—'))} → "
                f"{h(str(c.get('new_value') or '—'))}"
            )
        lines.append("")

    if report.drift_warnings:
        lines.append("<b>Drift warnings (Sharia Drift Radar):</b>")
        for d in report.drift_warnings:
            lines.append(
                f"  ⚠️ {h(str(d['symbol']))} approaching breach "
                f"(filing {h(str(d.get('new_value') or '—'))})"
            )
        lines.append("")

    def _list(syms: list[str]) -> str:
        return ", ".join(h(s) for s in syms) if syms else "(none)"

    lines.append(f"<b>Halal:</b> {_list(report.halal)}")
    lines.append(f"<b>Mixed:</b> {_list(report.mixed)}")
    lines.append(f"<b>Haram:</b> {_list(report.haram)}")
    if report.pending:
        lines.append(f"<b>Pending verification:</b> {_list(report.pending)}")

    return "\n".join(lines)
