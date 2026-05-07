"""SQLite connection helpers.

A single SQLite file at $DATA_DIR/xyzstocksus.db backs every persistent table.
WAL mode is enabled so the worker (writes) and the web process (reads) can
share the file without lock contention.

Usage:
    from db.connection import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM signals LIMIT 10").fetchall()
        # rows are sqlite3.Row → behave like dict and tuple
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.settings import settings, ensure_dirs

_init_lock = threading.Lock()
_initialized = False


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA busy_timeout = 5000")


def _ensure_db_initialized(db_path: Path) -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        ensure_dirs()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file with pragmas applied once
        conn = sqlite3.connect(str(db_path))
        try:
            _apply_pragmas(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized = True


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a fresh connection per call. SQLite handles concurrency via WAL."""
    target = db_path or settings.db_path
    _ensure_db_initialized(target)
    conn = sqlite3.connect(str(target), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Wrap multiple statements in a single transaction with auto rollback on error."""
    with get_conn(db_path) as conn:
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def reset_init_state() -> None:
    """Test helper — let tests recreate the DB file under a fresh path."""
    global _initialized
    with _init_lock:
        _initialized = False
