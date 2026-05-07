"""Budget guard — checked before every Sonnet (deep) call.

Hierarchy:
  - Daily soft alert  ($2.50)  → log + Telegram heads-up, calls still proceed
  - Daily hard cap    ($5.00)  → block deep calls; only Haiku pre-screen + /quick
  - Monthly hard cap  ($80.00) → block all LLM calls until month rolls
  - Monthly warn      (75% of monthly hard) → auto-switch to /quick-only mode
                                              (sets runtime_config 'quick_only' = True)
  - Daily deep cap    (30 calls) → block Sonnet, allow Haiku

The guard is read-mostly; the only writes happen when it auto-flips
'quick_only' on crossing 75% of the monthly cap.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from core import cost_tracker
from core.logger import get_logger
from db.repos import runtime_config

log = get_logger("core.budget")


@dataclass
class BudgetState:
    today_usd: float
    month_usd: float
    deep_count_today: int
    quick_only: bool
    daily_soft_breached: bool
    daily_hard_breached: bool
    monthly_warn_breached: bool
    monthly_hard_breached: bool
    deep_cap_reached: bool


def state() -> BudgetState:
    today = cost_tracker.today_usd()
    month = cost_tracker.month_usd()
    deep = cost_tracker.deep_count_today()
    quick_only = bool(runtime_config.get_value("quick_only", default=False))
    monthly_warn = settings.monthly_hard_usd * settings.monthly_warn_pct
    return BudgetState(
        today_usd=today,
        month_usd=month,
        deep_count_today=deep,
        quick_only=quick_only,
        daily_soft_breached=today >= settings.daily_soft_usd,
        daily_hard_breached=today >= settings.daily_hard_usd,
        monthly_warn_breached=month >= monthly_warn,
        monthly_hard_breached=month >= settings.monthly_hard_usd,
        deep_cap_reached=deep >= settings.daily_deep_cap,
    )


def can_run_deep() -> tuple[bool, str | None]:
    """Decide whether we may invoke a Sonnet call right now.

    Returns (allowed, reason_if_blocked). If allowed=False, the reason is a
    short human-readable string suitable for Telegram.
    """
    s = state()
    if s.monthly_hard_breached:
        return False, f"Monthly hard cap reached (${s.month_usd:.2f}/${settings.monthly_hard_usd:.2f})"
    if s.daily_hard_breached:
        return False, f"Daily hard cap reached (${s.today_usd:.2f}/${settings.daily_hard_usd:.2f})"
    if s.deep_cap_reached:
        return False, f"Daily deep-analysis cap reached ({s.deep_count_today}/{settings.daily_deep_cap})"
    if s.quick_only:
        return False, "Bot is in /quick-only mode (75% of monthly budget used)"
    return True, None


def can_run_haiku() -> tuple[bool, str | None]:
    """Haiku pre-screen is cheap; only blocked by the monthly hard cap."""
    s = state()
    if s.monthly_hard_breached:
        return False, f"Monthly hard cap reached (${s.month_usd:.2f}/${settings.monthly_hard_usd:.2f})"
    return True, None


def reconcile_quick_only_flag() -> bool:
    """Auto-flip quick_only when the 75% monthly threshold is crossed.

    Returns the new flag value. Callers (scheduler, after-call hook) invoke
    this periodically so the system self-protects without manual toggles.
    """
    s = state()
    current = bool(runtime_config.get_value("quick_only", default=False))
    should_be = s.monthly_warn_breached and not s.monthly_hard_breached
    if should_be != current:
        runtime_config.set_value("quick_only", should_be)
        log.info(
            "quick_only auto-flipped",
            extra={"to": should_be, "month_usd": s.month_usd,
                   "monthly_hard": settings.monthly_hard_usd},
        )
    return should_be


def disable_quick_only() -> None:
    """Manual reset (admin /resume after a budget rollover)."""
    runtime_config.set_value("quick_only", False)
