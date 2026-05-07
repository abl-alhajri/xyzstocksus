"""Haiku pre-screen — turns 15 candidates into 2-4 deep-analysis survivors.

Single Haiku call per scan. The system prompt is cached so we pay full price
once per ~5min then cache reads thereafter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config.settings import settings
from core import budget_guard, cost_tracker
from core.logger import get_logger
from db.repos import signals as signals_repo
from llm.client import LLMResponse, complete

log = get_logger("llm.prescreen")

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class PrescreenCandidate:
    symbol: str
    sector: str
    agent_set: str
    sharia_status: str
    heuristic: dict   # ScoreBreakdown.__dict__ form
    last_price: float | None
    btc_regime: str | None
    earnings_blackout: bool


@dataclass
class PrescreenVerdict:
    symbol: str
    worth_deep: bool
    reason: str


@dataclass
class PrescreenResult:
    verdicts: list[PrescreenVerdict]
    survivors: list[PrescreenVerdict]
    raw_response: LLMResponse | None
    blocked_reason: str | None = None


def _system_text() -> str:
    base = (PROMPTS_DIR / "system_base.txt").read_text(encoding="utf-8")
    pres = (PROMPTS_DIR / "prescreen.txt").read_text(encoding="utf-8")
    return f"{base}\n\n---\n\n{pres}"


def run(candidates: Iterable[PrescreenCandidate]) -> PrescreenResult:
    """Decide which candidates deserve deep analysis. Persists prescreen rows."""
    cands = list(candidates)
    if not cands:
        return PrescreenResult(verdicts=[], survivors=[], raw_response=None)

    allowed, reason = budget_guard.can_run_haiku()
    if not allowed:
        log.warning("prescreen blocked", extra={"reason": reason})
        return PrescreenResult(verdicts=[], survivors=[], raw_response=None,
                               blocked_reason=reason)

    payload = {
        "candidates": [
            {
                "symbol": c.symbol,
                "sector": c.sector,
                "agent_set": c.agent_set,
                "sharia_status": c.sharia_status,
                "heuristic": c.heuristic,
                "last_price": c.last_price,
                "btc_regime": c.btc_regime,
                "earnings_blackout": c.earnings_blackout,
            }
            for c in cands
        ],
        "scan_max_survivors": settings.deep_max_per_scan,
    }
    user_msg = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}

    try:
        response = complete(
            model=settings.model_haiku,
            user_messages=[user_msg],
            system_parts=[(_system_text(), True)],
            max_tokens=900,
            temperature=0.2,
            parse_json=True,
        )
    except Exception as exc:
        log.error("prescreen call failed", extra={"err": str(exc)})
        return PrescreenResult(verdicts=[], survivors=[], raw_response=None,
                               blocked_reason=f"call failed: {exc}")

    cost_tracker.record_call(response.usage, agent="prescreen", symbol=None)

    verdicts = _parse_verdicts(response.parsed_json, cands)
    survivors = _select_survivors(verdicts, cands,
                                  min_n=settings.deep_min_per_scan,
                                  max_n=settings.deep_max_per_scan)

    _persist(cands, verdicts, survivors, response)
    return PrescreenResult(
        verdicts=verdicts,
        survivors=survivors,
        raw_response=response,
    )


def _parse_verdicts(parsed: object, cands: list[PrescreenCandidate]) -> list[PrescreenVerdict]:
    if not isinstance(parsed, dict):
        return [PrescreenVerdict(c.symbol, False, "prescreen JSON missing") for c in cands]
    items = parsed.get("verdicts") or []
    by_sym: dict[str, PrescreenVerdict] = {}
    for it in items:
        sym = (it.get("symbol") or "").upper()
        if not sym:
            continue
        by_sym[sym] = PrescreenVerdict(
            symbol=sym,
            worth_deep=bool(it.get("worth_deep", False)),
            reason=str(it.get("reason") or "")[:240],
        )
    out: list[PrescreenVerdict] = []
    for c in cands:
        if c.symbol in by_sym:
            out.append(by_sym[c.symbol])
        else:
            out.append(PrescreenVerdict(c.symbol, False, "no verdict — defaulting to skip"))
    return out


def _select_survivors(
    verdicts: list[PrescreenVerdict],
    cands: list[PrescreenCandidate],
    *,
    min_n: int,
    max_n: int,
) -> list[PrescreenVerdict]:
    """Apply dynamic 2-4 sizing per the refined plan.

    - Take all worth_deep=True verdicts.
    - Cap at max_n (sorted by heuristic score desc).
    - Floor at min_n only if there are at least min_n candidates with
      score >= 50 — otherwise allow fewer (don't pad weak names).
    """
    score_by_sym = {c.symbol: float(c.heuristic.get("total", 0)) for c in cands}
    approved = [v for v in verdicts if v.worth_deep]
    approved.sort(key=lambda v: score_by_sym.get(v.symbol, 0), reverse=True)
    if len(approved) > max_n:
        approved = approved[:max_n]
    if len(approved) < min_n:
        # Backfill ONLY if the next-strongest names have score >= 50
        seen = {v.symbol for v in approved}
        candidates_strong = sorted(
            (c for c in cands
             if c.symbol not in seen
             and c.sharia_status != "HARAM"
             and not c.earnings_blackout
             and float(c.heuristic.get("total", 0)) >= 50),
            key=lambda c: float(c.heuristic.get("total", 0)),
            reverse=True,
        )
        for c in candidates_strong:
            if len(approved) >= min_n:
                break
            approved.append(PrescreenVerdict(
                symbol=c.symbol, worth_deep=True,
                reason=f"backfill (heuristic {c.heuristic.get('total')})",
            ))
    return approved


def _persist(
    cands: list[PrescreenCandidate],
    verdicts: list[PrescreenVerdict],
    survivors: list[PrescreenVerdict],
    response: LLMResponse,
) -> None:
    survivor_set = {v.symbol for v in survivors}
    cost_per = (response.usage.cost_usd / len(cands)) if cands else 0.0
    for v in verdicts:
        try:
            signals_repo.insert_prescreen(
                symbol=v.symbol,
                haiku_verdict=v.worth_deep,
                haiku_reasoning=v.reason,
                deep_analyze=v.symbol in survivor_set,
                cost_usd=cost_per,
            )
        except Exception as exc:  # pragma: no cover
            log.warning("prescreen persist failed",
                        extra={"symbol": v.symbol, "err": str(exc)})
