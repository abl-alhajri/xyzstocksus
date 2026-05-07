"""Repository for user_positions (manual /buy /sell tracking for compliance)."""
from __future__ import annotations

from datetime import datetime, timezone

from db.connection import get_conn, transaction


def open_position(
    *,
    symbol: str,
    entry_price: float,
    quantity: float,
    sharia_status_at_entry: str | None,
    notes: str | None = None,
    entry_date: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO user_positions
              (symbol, entry_date, entry_price, quantity,
               sharia_status_at_entry, status, notes)
            VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                symbol.upper(),
                entry_date or _now(),
                entry_price,
                quantity,
                sharia_status_at_entry,
                notes,
            ),
        )
        return int(cur.lastrowid or 0)


def close_position(
    position_id: int,
    *,
    closed_price: float | None = None,
    closed_date: str | None = None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE user_positions
            SET status = 'CLOSED',
                closed_date = ?,
                closed_price = ?
            WHERE id = ?
            """,
            (closed_date or _now(), closed_price, position_id),
        )


def close_all_for_symbol(symbol: str, closed_price: float | None = None) -> int:
    """Close every OPEN position for a symbol. Returns count closed."""
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE user_positions
            SET status = 'CLOSED',
                closed_date = ?,
                closed_price = ?
            WHERE symbol = ? AND status = 'OPEN'
            """,
            (_now(), closed_price, symbol.upper()),
        )
        return cur.rowcount


def list_open() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_positions WHERE status = 'OPEN' ORDER BY entry_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_for_symbol(symbol: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_positions WHERE symbol = ? ORDER BY entry_date DESC",
            (symbol.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]


def open_symbols() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM user_positions WHERE status = 'OPEN'"
        ).fetchall()
        return [r["symbol"] for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
