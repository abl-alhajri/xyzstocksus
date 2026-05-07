"""Cost tracker — every LLM call writes a row, every component reads totals.

Writes go through `record_call()` which:
- inserts an api_costs row,
- returns the same usage object (so callers can chain),
- never raises (cost tracking must not break a scan).

Reads via `today_usd()`, `month_usd()`, `deep_count_today()` plus per-agent
breakdown for the dashboard.
"""
from __future__ import annotations

from db.repos import costs as costs_repo
from core.logger import get_logger
from llm.client import LLMUsage

log = get_logger("core.costs")


def record_call(
    usage: LLMUsage,
    *,
    agent: str | None = None,
    symbol: str | None = None,
) -> LLMUsage:
    """Persist a single LLM call into api_costs. Returns `usage` unchanged."""
    try:
        costs_repo.insert_cost(
            model=usage.model,
            agent=agent,
            symbol=symbol,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cost_usd=usage.cost_usd,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("cost record failed", extra={"err": str(exc), "agent": agent})
    return usage


def today_usd() -> float:
    return costs_repo.total_today()


def month_usd() -> float:
    return costs_repo.total_this_month()


def deep_count_today() -> int:
    return costs_repo.deep_analyses_today()


def per_agent_today() -> dict[str, float]:
    return costs_repo.per_agent_today()
