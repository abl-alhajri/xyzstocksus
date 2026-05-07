"""Worker entrypoint — runs migrations, then the APScheduler loop."""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timezone


def _log(msg: str, **kw) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    extra = " ".join(f"{k}={v}" for k, v in kw.items())
    print(f"[{ts}] worker | {msg} {extra}".strip(), flush=True)


def _install_signal_handlers(sched) -> None:
    def _handler(signum, _frame):
        _log("received signal, shutting down", signal=signum)
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    data_dir = os.getenv("DATA_DIR", "/data")
    tz = os.getenv("TZ", "UTC")
    _log("worker boot", data_dir=data_dir, tz=tz)

    # Run migrations + seed before scheduler starts
    from db.migrate import run_migrations
    report = run_migrations()
    _log("migrations done", applied=report["applied"], seeded=report["seeded_symbols"])

    from core.scheduler import build_scheduler
    sched = build_scheduler()
    _install_signal_handlers(sched)
    sched.start()
    _log("scheduler started", jobs=[j.id for j in sched.get_jobs()])

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
