"""Repository for api_costs — read/write LLM spend rows."""
from __future__ import annotations

from datetime import datetime, timezone

from db.connection import get_conn, transaction


def insert_cost(
    *,
    model: str,
    agent: str | None,
    symbol: str | None,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    cache_creation_tokens: int,
    cost_usd: float,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO api_costs
              (timestamp, model, agent, symbol,
               input_tokens, output_tokens, cached_tokens,
               cache_creation_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp or _now(),
                model,
                agent,
                symbol.upper() if symbol else None,
                input_tokens,
                output_tokens,
                cached_tokens,
                cache_creation_tokens,
                cost_usd,
            ),
        )
        return int(cur.lastrowid or 0)


def total_today() -> float:
    """USD spent today (UTC). Used by budget guard."""
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM api_costs
            WHERE substr(timestamp, 1, 10) = substr(datetime('now'), 1, 10)
            """
        ).fetchone()
        return float(r["total"] or 0.0)


def total_this_month() -> float:
    """USD spent in the current calendar month (UTC). Used by budget guard."""
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM api_costs
            WHERE substr(timestamp, 1, 7) = substr(datetime('now'), 1, 7)
            """
        ).fetchone()
        return float(r["total"] or 0.0)


def deep_analyses_today() -> int:
    """Count of Sonnet calls today — used to enforce 30/day deep cap."""
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM api_costs
            WHERE model LIKE '%sonnet%'
              AND substr(timestamp, 1, 10) = substr(datetime('now'), 1, 10)
            """
        ).fetchone()
        return int(r["n"] or 0)


def per_agent_today() -> dict[str, float]:
    """Cost breakdown per agent for today — used by /cost and dashboard."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT agent, SUM(cost_usd) AS total
            FROM api_costs
            WHERE substr(timestamp, 1, 10) = substr(datetime('now'), 1, 10)
            GROUP BY agent
            """
        ).fetchall()
        return {(r["agent"] or "unknown"): float(r["total"] or 0.0) for r in rows}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
