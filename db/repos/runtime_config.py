"""Repository for runtime_config — key/value runtime overrides + command_log."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.connection import get_conn, transaction

_SENTINEL = object()


def get_value(key: str, default: Any = _SENTINEL) -> Any:
    with get_conn() as conn:
        r = conn.execute("SELECT value FROM runtime_config WHERE key = ?", (key,)).fetchone()
        if not r:
            if default is _SENTINEL:
                return None
            return default
        try:
            return json.loads(r["value"])
        except Exception:
            return r["value"]


def set_value(key: str, value: Any) -> None:
    payload = json.dumps(value)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO runtime_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, payload, _now()),
        )


def delete(key: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM runtime_config WHERE key = ?", (key,))


def all_keys() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM runtime_config").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    return out


# --- command_log ---------------------------------------------------------

def log_command(
    *,
    chat_id: str | None,
    command: str,
    args: str | None,
    success: bool,
    error: str | None = None,
    timestamp: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO command_log (timestamp, chat_id, command, args, success, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp or _now(), chat_id, command, args, 1 if success else 0, error),
        )
        return int(cur.lastrowid or 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
