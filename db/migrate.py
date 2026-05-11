"""Migration runner — idempotent, file-driven, seeds the watchlist.

Discovers every `db/migrations/NNN_*.sql` file, applies any not yet recorded in
`schema_migrations`, then seeds `stocks_metadata` from `config.watchlist`.

Safe to call on every process boot.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from config.agent_sets import resolve_set_for_sector
from config.watchlist import WATCHLIST
from db.connection import get_conn

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SCHEMA_FILE = Path(__file__).parent / "schema.sql"

_NAME_RE = re.compile(r"^(\d+)_(.+)\.sql$")

# Migration 005 — symbols being hard-deleted. Must match the IN-list in
# 005_watchlist_refresh.sql. Pre-check refuses to run if any OPEN position
# exists on these tickers.
_005_REMOVED = (
    "MARA", "DIS", "MCD", "NKE", "PEP", "PYPL", "COIN", "TMO", "PFE",
    "LLY", "COST", "CAT", "META", "MA", "AVGO", "MSFT", "UNH", "V", "HD",
)
_005_NEW = (
    "TSM", "TXN", "PLTR", "MRVL", "STX", "ARM", "VRT", "VST", "TLN",
    "ANET", "AMAT", "WDC", "CRWD", "NOW", "COHR", "ON",
)


class MigrationAborted(RuntimeError):
    """Raised by a pre-check hook to refuse a migration safely."""


def _precheck_005(conn) -> None:
    """Abort migration 005 if any of the 19 removed tickers has an OPEN
    position. Closing those positions is a user action — we never silently
    delete trades the user is still holding.
    """
    placeholders = ",".join("?" * len(_005_REMOVED))
    rows = conn.execute(
        f"SELECT symbol, COUNT(*) AS n FROM user_positions "
        f"WHERE status = 'OPEN' AND symbol IN ({placeholders}) "
        f"GROUP BY symbol ORDER BY symbol",
        _005_REMOVED,
    ).fetchall()
    if rows:
        offenders = ", ".join(f"{r['symbol']} ({r['n']})" for r in rows)
        msg = (
            f"Migration 005 aborted: OPEN positions exist on tickers being "
            f"removed: {offenders}. Close them in Telegram (/sell SYMBOL) "
            f"before redeploying."
        )
        print(f"[migrate] {msg}")
        raise MigrationAborted(msg)
    print("[migrate] 005 pre-check: no open positions on removed tickers -- OK")


def _postmessage_005() -> None:
    new_list = ", ".join(_005_NEW)
    print(
        "[migrate] OK migration 005 complete. New tickers added on next seed: "
        f"{new_list}. Run /refresh_sharia from Telegram to populate Sharia "
        f"ratios for the new tickers."
    )


# Map migration name → (precheck, postmessage) hooks. Each is optional.
_HOOKS: dict[str, dict] = {
    "005_watchlist_refresh": {
        "pre": _precheck_005,
        "post": _postmessage_005,
    },
}


def _discover_migrations() -> list[tuple[int, str, Path]]:
    """Return (sortable_id, stable_name, path). The name is the file stem so it
    survives renames more gracefully and matches what humans see on disk."""
    files = []
    if MIGRATIONS_DIR.exists():
        for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
            m = _NAME_RE.match(p.name)
            if m:
                files.append((int(m.group(1)), p.stem, p))
    return files


def _applied_migrations(conn) -> set[str]:
    try:
        rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
        return {r["name"] for r in rows}
    except Exception:
        return set()


def _apply_schema(conn) -> None:
    """Run schema.sql — idempotent because every CREATE is IF NOT EXISTS."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    conn.executescript(sql)


def _seed_watchlist(conn) -> int:
    """Insert any watchlist symbols not yet in stocks_metadata.

    Existing rows are NEVER overwritten — user toggles (enabled, sharia_status)
    must survive boots. We only INSERT the seed if missing.
    """
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()
    for symbol, seed in WATCHLIST.items():
        exists = conn.execute(
            "SELECT 1 FROM stocks_metadata WHERE symbol = ?", (symbol,)
        ).fetchone()
        if exists:
            continue
        agent_set = resolve_set_for_sector(seed.sector).name
        conn.execute(
            """
            INSERT INTO stocks_metadata
                (symbol, sector, btc_beta, agent_set, enabled,
                 sharia_status, expected_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, 'PENDING', ?, ?, ?)
            """,
            (symbol, seed.sector, seed.btc_beta, agent_set,
             seed.expected_status, now, now),
        )
        inserted += 1
    return inserted


def run_migrations() -> dict:
    """Apply migrations + seed watchlist. Returns a small report dict.

    Uses autocommit (isolation_level=None) for `executescript` calls — sqlite3
    auto-commits multi-statement scripts — and a single explicit transaction
    around the seed inserts (atomicity matters there but executescript does not
    play nicely with manual BEGIN/COMMIT).
    """
    discovered = _discover_migrations()
    report = {"applied": [], "already_present": [], "seeded_symbols": 0}

    with get_conn() as conn:
        _apply_schema(conn)
        applied = _applied_migrations(conn)

        for mig_id, name, path in discovered:
            if name in applied:
                report["already_present"].append(name)
                continue
            hooks = _HOOKS.get(name, {})
            pre = hooks.get("pre")
            if pre is not None:
                pre(conn)
            sql_body = path.read_text(encoding="utf-8").strip()
            rows_before = conn.total_changes
            if sql_body and not _is_only_comments(sql_body):
                conn.executescript(sql_body)
            rows_changed = conn.total_changes - rows_before
            conn.execute(
                "INSERT INTO schema_migrations (id, name) VALUES (?, ?)",
                (mig_id, name),
            )
            report["applied"].append(name)
            if rows_changed:
                print(f"[migrate] {name}: {rows_changed} rows changed")
            post = hooks.get("post")
            if post is not None:
                post()

        # Seed in one transaction (no executescript inside).
        conn.execute("BEGIN")
        try:
            report["seeded_symbols"] = _seed_watchlist(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return report


def _is_only_comments(text: str) -> bool:
    """A SQL file containing only `--` lines should not be executed."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        return False
    return True


if __name__ == "__main__":
    result = run_migrations()
    print(f"applied: {result['applied']}")
    print(f"already present: {result['already_present']}")
    print(f"seeded symbols: {result['seeded_symbols']}")
