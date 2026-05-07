"""Web entrypoint — Flask dashboard + Telegram polling in a background thread.

Gunicorn imports `app`. On first import we kick off:
- DB migrations
- Telegram bot polling (if a token is configured)
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

# 1. Flask app + routes
from dashboard.app import create_app

app = create_app()

# 2. Migrations on first boot
try:
    from db.migrate import run_migrations
    _report = run_migrations()
    print(f"[boot] migrations: applied={_report['applied']} "
          f"seeded={_report['seeded_symbols']}", flush=True)
except Exception as exc:  # pragma: no cover
    print(f"[boot] migration warning: {exc}", flush=True)


# 3. Telegram polling in a daemon thread (only if token is set)
def _start_telegram() -> None:
    try:
        from telegram_bot.bot import build_application
        application = build_application()
        if application is None:
            return
        application.run_polling(close_loop=False, stop_signals=None)
    except Exception as exc:
        print(f"[boot] telegram polling failed: {exc}", flush=True)


if os.getenv("TELEGRAM_BOT_TOKEN"):
    t = threading.Thread(target=_start_telegram, daemon=True, name="telegram-bot")
    t.start()
    print("[boot] telegram polling thread started", flush=True)
else:
    print("[boot] telegram polling skipped (no token)", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
