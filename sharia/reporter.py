"""Weekly compliance report formatter.

Pulls the last week of compliance_alerts + current per-symbol status and
renders a Telegram-friendly Markdown brief plus a JSON payload for the
dashboard's Sharia tab.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


def render_markdown(report: WeeklyReport) -> str:
    """Telegram-friendly Markdown rendering of a WeeklyReport.

    Structure follows the alert template in the project plan: counts at top,
    tier changes / drift warnings inline, status lists at bottom.
    """
    lines: list[str] = []
    lines.append("📋 *Weekly Sharia Compliance Report*")
    lines.append(f"_Generated: {report.generated_at}_")
    lines.append("")
    lines.append("*Status counts:*")
    for k in ("HALAL", "MIXED", "HARAM", "PENDING"):
        lines.append(f"  {ARABIC_LABEL.get(k, k)}: {report.counts.get(k, 0)}")
    lines.append("")

    if report.tier_changes:
        lines.append("*Tier changes (last 7d):*")
        for c in report.tier_changes:
            lines.append(f"  • {c['symbol']}: {c.get('old_value')} → {c.get('new_value')}")
        lines.append("")

    if report.drift_warnings:
        lines.append("*Drift warnings (Sharia Drift Radar):*")
        for d in report.drift_warnings:
            lines.append(f"  ⚠️ {d['symbol']} approaching breach (filing {d.get('new_value')})")
        lines.append("")

    lines.append("*Halal:* " + (", ".join(report.halal) if report.halal else "(none)"))
    lines.append("*Mixed:* " + (", ".join(report.mixed) if report.mixed else "(none)"))
    lines.append("*Haram:* " + (", ".join(report.haram) if report.haram else "(none)"))
    if report.pending:
        lines.append("*Pending verification:* " + ", ".join(report.pending))

    return "\n".join(lines)
