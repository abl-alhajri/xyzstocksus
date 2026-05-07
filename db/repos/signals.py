"""Repository for signals + agent_outputs + prescreen_results + dedup helpers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from db.connection import get_conn, transaction


# --- signals -------------------------------------------------------------

def insert_signal(
    *,
    symbol: str,
    decision: str,
    trade_type: str | None,
    confidence: float,
    sharia_status: str | None,
    full_synthesis: dict | None,
    veto_reason: str | None = None,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO signals
              (timestamp, symbol, decision, trade_type, confidence,
               sharia_status, full_synthesis_json, veto_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp or _now(),
                symbol.upper(),
                decision,
                trade_type,
                confidence,
                sharia_status,
                json.dumps(full_synthesis) if full_synthesis else None,
                veto_reason,
            ),
        )
        return int(cur.lastrowid or 0)


def mark_sent(signal_id: int, telegram_msg_id: int | None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE signals SET sent_to_telegram = 1, telegram_msg_id = ? WHERE id = ?",
            (telegram_msg_id, signal_id),
        )


def recent(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def last_sent_for_symbol(symbol: str) -> dict | None:
    """Used by dedup: most recent signal we sent to Telegram for this symbol."""
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT * FROM signals
            WHERE symbol = ? AND sent_to_telegram = 1
            ORDER BY timestamp DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
        return dict(r) if r else None


# --- agent_outputs -------------------------------------------------------

def insert_agent_output(
    *,
    signal_id: int | None,
    symbol: str,
    agent_name: str,
    round_num: int,
    output: dict,
    confidence: float | None,
    decision: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
    cost_usd: float | None,
    latency_ms: int | None,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_outputs
              (signal_id, symbol, timestamp, agent_name, round_num,
               output_json, confidence, decision,
               input_tokens, output_tokens, cached_tokens, cost_usd, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                symbol.upper(),
                timestamp or _now(),
                agent_name,
                round_num,
                json.dumps(output),
                confidence,
                decision,
                input_tokens,
                output_tokens,
                cached_tokens,
                cost_usd,
                latency_ms,
            ),
        )
        return int(cur.lastrowid or 0)


def outputs_for_signal(signal_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_outputs WHERE signal_id = ? ORDER BY round_num, agent_name",
            (signal_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# --- prescreen_results ---------------------------------------------------

def insert_prescreen(
    *,
    symbol: str,
    haiku_verdict: bool,
    haiku_reasoning: str | None,
    deep_analyze: bool,
    cost_usd: float | None,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO prescreen_results
              (symbol, timestamp, haiku_verdict, haiku_reasoning, deep_analyze, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                timestamp or _now(),
                1 if haiku_verdict else 0,
                haiku_reasoning,
                1 if deep_analyze else 0,
                cost_usd,
            ),
        )
        return int(cur.lastrowid or 0)


# --- dedup ---------------------------------------------------------------

def should_dedup(
    symbol: str,
    *,
    new_confidence: float,
    window_hours: int,
    confidence_jump: float,
) -> bool:
    """Return True if the new signal should be SUPPRESSED (i.e. dedup match).

    A new signal is suppressed if a previous signal for the same symbol was
    sent within `window_hours` AND the confidence has not jumped by at least
    `confidence_jump` (e.g. 0.10 = 10pp).
    """
    last = last_sent_for_symbol(symbol)
    if not last:
        return False
    last_ts = _parse_iso(last["timestamp"])
    if last_ts is None:
        return False
    if datetime.now(timezone.utc) - last_ts > timedelta(hours=window_hours):
        return False
    last_conf = float(last.get("confidence") or 0.0)
    return (new_confidence - last_conf) < confidence_jump


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
