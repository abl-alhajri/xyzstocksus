"""Web entrypoint — exposes Flask `app` for gunicorn.

Phase 1 commit 1: minimal /health only. Dashboard + Telegram bot are wired in later commits.
"""
from __future__ import annotations

import os
import platform
import time
from datetime import datetime, timezone

from flask import Flask, jsonify

START_TIME = time.time()
APP_VERSION = "0.1.0"

app = Flask(__name__)


@app.get("/health")
def health() -> tuple:
    payload = {
        "status": "ok",
        "service": "xyzstocksus",
        "version": APP_VERSION,
        "uptime_s": round(time.time() - START_TIME, 1),
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "tz": os.getenv("TZ", "UTC"),
        "python": platform.python_version(),
    }
    return jsonify(payload), 200


@app.get("/")
def root() -> tuple:
    return jsonify({"service": "xyzstocksus", "see": "/health"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
