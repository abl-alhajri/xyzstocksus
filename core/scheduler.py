"""APScheduler wiring — 4 daily scans + Sharia daily/weekly + BTC ping.

Times in US/Eastern (per spec). Skips weekends + US holidays via
core.market_calendar.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from config.settings import settings
from core import market_calendar
from core.logger import get_logger
from core.orchestrator import run_scan
from sharia.monitor import run_daily_check, run_weekly_full_scan
from data.btc_feed import fetch_spot

log = get_logger("core.scheduler")
ET = ZoneInfo("America/New_York")
DUBAI = ZoneInfo("Asia/Dubai")


def _scan_job_safe(label: str) -> None:
    """Wrapper that guards against weekends/holidays and logs the run."""
    today = datetime.now(tz=ET).date()
    if not market_calendar.is_trading_day(today):
        log.info("scan skipped (non-trading day)", extra={"slot": label, "date": today.isoformat()})
        return
    log.info("scan start", extra={"slot": label})
    try:
        report = run_scan()
        log.info("scan done", extra={
            "slot": label,
            "candidates": report.candidates_pool,
            "prescreen": report.prescreen_pool,
            "deep": report.deep_survivors,
            "signals": len(report.signals_recorded),
            "btc_dump": report.btc_dump_active,
            "duration": report.finished_at,
        })
    except Exception as exc:
        log.error("scan failed", extra={"slot": label, "err": str(exc)})


def _btc_ping_job() -> None:
    try:
        snap = fetch_spot()
        if snap:
            log.info("btc_ping", extra={"price": snap.price})
    except Exception as exc:
        log.warning("btc_ping failed", extra={"err": str(exc)})


def _sharia_daily_job() -> None:
    try:
        report = run_daily_check()
        log.info("sharia_daily done", extra={
            "checked": len(report.checked),
            "reverified": len(report.re_verified),
            "tier_changes": len(report.tier_changes),
            "drift": len(report.drift_warnings),
        })
    except Exception as exc:
        log.error("sharia_daily failed", extra={"err": str(exc)})


def _sharia_weekly_job() -> None:
    try:
        report = run_weekly_full_scan()
        log.info("sharia_weekly done", extra={
            "checked": len(report.checked),
            "reverified": len(report.re_verified),
            "tier_changes": len(report.tier_changes),
            "drift": len(report.drift_warnings),
        })
    except Exception as exc:
        log.error("sharia_weekly failed", extra={"err": str(exc)})


def build_scheduler() -> BackgroundScheduler:
    """Construct (but do not start) the APScheduler with all jobs."""
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.jobstore_path}")
    sched = BackgroundScheduler(
        timezone=ET,
        jobstores={"default": jobstore},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    # The 4 daily scans (refined: dropped mid-day 11:30)
    for hour, minute, label in settings.scan_times_et:
        sched.add_job(
            _scan_job_safe,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=ET),
            args=[label],
            id=f"scan_{label}",
            replace_existing=True,
        )

    # BTC spot ping every minute (lightweight; feeds dump detector)
    sched.add_job(
        _btc_ping_job,
        trigger=CronTrigger(minute="*", timezone=ET),
        id="btc_ping",
        replace_existing=True,
    )

    # Sharia daily 09:00 Dubai
    sched.add_job(
        _sharia_daily_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=DUBAI),
        id="sharia_daily",
        replace_existing=True,
    )

    # Sharia weekly Saturday 10:00 Dubai
    sched.add_job(
        _sharia_weekly_job,
        trigger=CronTrigger(day_of_week="sat", hour=10, minute=0, timezone=DUBAI),
        id="sharia_weekly",
        replace_existing=True,
    )

    return sched
