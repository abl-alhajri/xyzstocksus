"""Web entrypoint — Flask dashboard + Telegram polling in a background thread.

Gunicorn imports `app`. On first import we kick off:
- DB migrations
- Telegram bot polling (if a token is configured)
"""
from __future__ import annotations

import asyncio
import os
import threading
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
#
# python-telegram-bot v20+ is async-native. When started inside a non-main
# thread we have to:
#   1. create a fresh asyncio event loop,
#   2. set it as the thread's current loop (PTB internally calls
#      asyncio.get_event_loop()),
#   3. drive the application lifecycle (initialize → start → start_polling)
#      via run_until_complete(),
#   4. block on run_forever() so the daemon thread stays alive.
#
# We avoid `Application.run_polling()` here because it tries to manage signal
# handlers and the loop itself, neither of which is safe in a worker thread.
def _start_telegram() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = None
    try:
        from telegram_bot.bot import build_application
        application = build_application()
        if application is None:
            return
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_until_complete(application.updater.start_polling())
        print("[boot] telegram polling started", flush=True)
        loop.run_forever()
    except Exception as exc:
        print(f"[boot] telegram polling failed: {exc}", flush=True)
    finally:
        # Best-effort graceful shutdown when the daemon thread is torn down.
        try:
            if application is not None:
                if application.updater and application.updater.running:
                    loop.run_until_complete(application.updater.stop())
                if application.running:
                    loop.run_until_complete(application.stop())
                loop.run_until_complete(application.shutdown())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass


if os.getenv("TELEGRAM_BOT_TOKEN"):
    t = threading.Thread(target=_start_telegram, daemon=True, name="telegram-bot")
    t.start()
    print("[boot] telegram polling thread started", flush=True)
else:
    print("[boot] telegram polling skipped (no token)", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
