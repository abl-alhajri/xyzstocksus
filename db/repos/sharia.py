"""Repository for financial_ratios_history + compliance_alerts."""
from __future__ import annotations

from datetime import datetime, timezone

from db.connection import get_conn, transaction


# --- financial_ratios_history -------------------------------------------

def insert_ratios(
    *,
    symbol: str,
    market_cap: float | None,
    total_debt: float | None,
    interest_bearing_debt: float | None,
    cash_and_securities: float | None,
    total_revenue: float | None,
    impermissible_revenue: float | None,
    debt_ratio: float | None,
    cash_ratio: float | None,
    impermissible_ratio: float | None,
    sharia_status: str | None,
    risk_tier: str | None,
    filing_date: str | None,
    filing_type: str | None,
    notes: str | None = None,
    fetched_at: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO financial_ratios_history
              (symbol, fetched_at, filing_date, filing_type,
               market_cap, total_debt, interest_bearing_debt,
               cash_and_securities, total_revenue, impermissible_revenue,
               debt_ratio, cash_ratio, impermissible_ratio,
               sharia_status, risk_tier, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                fetched_at or _now(),
                filing_date,
                filing_type,
                market_cap,
                total_debt,
                interest_bearing_debt,
                cash_and_securities,
                total_revenue,
                impermissible_revenue,
                debt_ratio,
                cash_ratio,
                impermissible_ratio,
                sharia_status,
                risk_tier,
                notes,
            ),
        )
        return int(cur.lastrowid or 0)


def latest_ratios(symbol: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT * FROM financial_ratios_history
            WHERE symbol = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
        return dict(r) if r else None


def quarterly_history(symbol: str, limit: int = 8) -> list[dict]:
    """Return up to `limit` quarterly snapshots, oldest first.

    Used by the Sharia Drift Radar to compute the slope of debt/cash ratios.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM financial_ratios_history
            WHERE symbol = ?
            ORDER BY filing_date ASC
            LIMIT ?
            """,
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]


# --- compliance_alerts ---------------------------------------------------

def insert_alert(
    *,
    symbol: str,
    alert_type: str,
    old_value: str | None,
    new_value: str | None,
    severity: str,
    telegram_msg_id: int | None = None,
    sent_at: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO compliance_alerts
              (symbol, alert_type, old_value, new_value, severity, sent_at, telegram_msg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                alert_type,
                old_value,
                new_value,
                severity,
                sent_at or _now(),
                telegram_msg_id,
            ),
        )
        return int(cur.lastrowid or 0)


def recent_alerts(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM compliance_alerts ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def alerts_for_symbol(symbol: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM compliance_alerts WHERE symbol = ? ORDER BY sent_at DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
