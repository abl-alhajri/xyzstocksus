"""Repository for stocks_metadata and heuristic_scores."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from db.connection import get_conn, transaction


@dataclass
class StockRow:
    symbol: str
    sector: str
    btc_beta: float
    agent_set: str
    enabled: bool
    sharia_status: str
    sharia_status_verified_at: str | None
    expected_status: str | None


def _row_to_stock(r) -> StockRow:
    return StockRow(
        symbol=r["symbol"],
        sector=r["sector"],
        btc_beta=r["btc_beta"],
        agent_set=r["agent_set"],
        enabled=bool(r["enabled"]),
        sharia_status=r["sharia_status"],
        sharia_status_verified_at=r["sharia_status_verified_at"],
        expected_status=r["expected_status"],
    )


def list_all(enabled_only: bool = False) -> list[StockRow]:
    sql = "SELECT * FROM stocks_metadata"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY symbol"
    with get_conn() as conn:
        return [_row_to_stock(r) for r in conn.execute(sql).fetchall()]


def get(symbol: str) -> StockRow | None:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM stocks_metadata WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        return _row_to_stock(r) if r else None


def set_enabled(symbol: str, enabled: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE stocks_metadata SET enabled = ?, updated_at = ? WHERE symbol = ?",
            (1 if enabled else 0, _now(), symbol.upper()),
        )


def set_sharia_status(symbol: str, status: str, verified_at: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE stocks_metadata
            SET sharia_status = ?,
                sharia_status_verified_at = ?,
                updated_at = ?
            WHERE symbol = ?
            """,
            (status, verified_at or _now(), _now(), symbol.upper()),
        )


def by_sharia_status(status: str) -> list[StockRow]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stocks_metadata WHERE sharia_status = ? ORDER BY symbol",
            (status,),
        ).fetchall()
        return [_row_to_stock(r) for r in rows]


# --- heuristic_scores ----------------------------------------------------

def insert_heuristic(
    symbol: str,
    *,
    rsi: float | None,
    macd: float | None,
    macd_signal: float | None,
    volume_ratio: float | None,
    btc_corr_30d: float | None,
    score: float,
    raw: dict | None = None,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO heuristic_scores
              (symbol, timestamp, rsi, macd, macd_signal, volume_ratio,
               btc_corr_30d, score, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                timestamp or _now(),
                rsi,
                macd,
                macd_signal,
                volume_ratio,
                btc_corr_30d,
                score,
                json.dumps(raw) if raw else None,
            ),
        )
        return int(cur.lastrowid or 0)


def latest_heuristic(symbol: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM heuristic_scores WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return dict(r) if r else None


def latest_scores_all(symbols: Iterable[str] | None = None) -> dict[str, dict]:
    """Return latest heuristic row for each symbol (used by dashboard grid)."""
    out: dict[str, dict] = {}
    syms = list(symbols) if symbols else [s.symbol for s in list_all()]
    with get_conn() as conn:
        for sym in syms:
            r = conn.execute(
                "SELECT * FROM heuristic_scores WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                (sym.upper(),),
            ).fetchone()
            if r:
                out[sym.upper()] = dict(r)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
