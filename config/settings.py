"""Runtime settings for XYZStocksUS.

All values are env-driven with sensible defaults. The single `settings` instance is
imported across the codebase. Environment is loaded from `.env` when present
(local dev); on Railway, env vars come straight from the platform.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return value if value is not None and value != "" else default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- secrets ---
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    telegram_bot_token: str | None = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # --- runtime ---
    tz: str = field(default_factory=lambda: _env("TZ", "Asia/Dubai") or "Asia/Dubai")
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/data") or "/data"))
    log_level: str = field(default_factory=lambda: (_env("LOG_LEVEL", "INFO") or "INFO").upper())
    port: int = field(default_factory=lambda: _env_int("PORT", 8080))
    health_token: str | None = field(default_factory=lambda: _env("HEALTH_TOKEN"))

    # --- LLM models ---
    model_haiku: str = field(default_factory=lambda: _env("LLM_MODEL_HAIKU", "claude-haiku-4-5") or "claude-haiku-4-5")
    model_sonnet: str = field(default_factory=lambda: _env("LLM_MODEL_SONNET", "claude-sonnet-4-6") or "claude-sonnet-4-6")

    # --- budget caps (USD) ---
    daily_soft_usd: float = field(default_factory=lambda: _env_float("DAILY_SOFT_USD", 2.50))
    daily_hard_usd: float = field(default_factory=lambda: _env_float("DAILY_HARD_USD", 5.00))
    monthly_hard_usd: float = field(default_factory=lambda: _env_float("MONTHLY_HARD_USD", 80.00))
    monthly_warn_pct: float = field(default_factory=lambda: _env_float("MONTHLY_WARN_PCT", 0.75))

    # --- SEC EDGAR (User-Agent header is required by SEC) ---
    sec_user_agent: str = field(
        default_factory=lambda: _env("SEC_USER_AGENT", "XYZStocksUS contact@example.com")
        or "XYZStocksUS contact@example.com"
    )

    # --- derived paths under data_dir ---
    @property
    def db_path(self) -> Path:
        return self.data_dir / "xyzstocksus.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def jobstore_path(self) -> Path:
        return self.data_dir / "jobstore.sqlite"

    # --- scan schedule (US/Eastern) — refined: 4 scans/day, no mid-day ---
    scan_times_et: Tuple[Tuple[int, int, str], ...] = (
        (8, 0, "pre_market"),
        (9, 35, "market_open"),
        (15, 30, "pre_close"),
        (16, 30, "post_close"),
    )

    # --- behavior knobs ---
    min_confidence_alert: float = 0.65
    dedup_window_hours: int = 4
    dedup_confidence_jump: float = 0.10  # 10pp jump bypasses dedup
    earnings_blackout_hours: int = 48

    # R2 cross-critique trigger band (refined: 0.60-0.70)
    r2_trigger_low: float = 0.60
    r2_trigger_high: float = 0.70

    # Deep-analysis sizing per scan (refined: dynamic 2-4)
    deep_min_per_scan: int = 2
    deep_max_per_scan: int = 4

    # Haiku pre-screen pool size
    prescreen_top_n: int = 15

    # Daily deep-analysis cap (after which only /quick is allowed)
    daily_deep_cap: int = 30

    # BTC dump protection: drop threshold over rolling window → skip btc_full
    btc_dump_pct: float = 0.05  # 5% drop
    btc_dump_window_min: int = 60
    btc_dump_cooldown_min: int = 60


settings = Settings()


def ensure_dirs() -> None:
    """Create data subdirectories on first boot. Safe to call repeatedly."""
    for path in (settings.data_dir, settings.cache_dir, settings.logs_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass
