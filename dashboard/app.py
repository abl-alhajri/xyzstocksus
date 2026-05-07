"""Flask app factory — pulls in /health (commit 1) and the dashboard blueprint."""
from __future__ import annotations

from flask import Flask

from dashboard.routes import bp as dashboard_bp


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.register_blueprint(dashboard_bp)

    @app.get("/health")
    def health():
        from datetime import datetime, timezone
        return {"status": "ok", "service": "xyzstocksus",
                "now_utc": datetime.now(timezone.utc).isoformat()}

    return app
