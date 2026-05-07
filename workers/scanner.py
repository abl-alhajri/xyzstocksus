"""Worker entrypoint — runs the scheduled scan loop.

Phase 1 commit 1: skeleton heartbeat only. The real scheduler, market calendar, and
multi-agent debate orchestrator come online in later commits.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timezone

HEARTBEAT_SECONDS = 60


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] worker | {msg}", flush=True)


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        _log(f"received signal {signum}, shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    _install_signal_handlers()
    data_dir = os.getenv("DATA_DIR", "/data")
    tz = os.getenv("TZ", "UTC")
    _log(f"starting xyzstocksus worker (DATA_DIR={data_dir}, TZ={tz})")
    _log("scheduler not yet wired — heartbeat-only mode for commit 1")
    while True:
        time.sleep(HEARTBEAT_SECONDS)
        _log("heartbeat")


if __name__ == "__main__":
    main()
