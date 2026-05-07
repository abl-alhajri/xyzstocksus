"""Multi-agent debate orchestration: R1 parallel → veto → R2 critique (band) → R3 synth.

Public entry points:
  - run_debate(...)       — sync wrapper for command flow (/analyze, /quick)
  - run_debate_async(...) — for the scheduler scan loop

Both return a `DebateResult` capturing every agent output, total cost, and
the final synthesized decision. Sharia veto is enforced at three points:
pre-debate (HARAM short-circuit), post-Round-1 (skip R2/R3), and again at
the synthesizer (which also enforces Risk-grade-D pass).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agents.base import Agent, AgentInput, AgentOutput
from agents.btc_macro_analyst import BTCMacroAnalyst
from agents.devils_advocate import DevilsAdvocate
from agents.fundamentals_analyst import FundamentalsAnalyst
from agents.macro_voice import MacroVoice
from agents.risk_manager import RiskManager
from agents.sharia_officer import ShariaOfficer
from agents.synthesizer import Synthesizer
from agents.technical_analyst import TechnicalAnalyst
from config.agent_sets import (
    AGENT_BTC_MACRO,
    AGENT_DEVILS_ADVOCATE,
    AGENT_FUNDAMENTALS,
    AGENT_MACRO_VOICE,
    AGENT_RISK,
    AGENT_SHARIA,
    AGENT_SYNTHESIZER,
    AGENT_TECHNICAL,
    AgentSet,
)
from config.settings import settings
from config.thresholds import R2_BAND
from core import budget_guard
from core.logger import get_logger
from llm.client import LLMUsage

log = get_logger("agents.debate")


@dataclass
class DebateResult:
    symbol: str
    agent_set: str
    round1: list[AgentOutput] = field(default_factory=list)
    round2: list[AgentOutput] = field(default_factory=list)
    final: AgentOutput | None = None
    vetoed: bool = False
    veto_reason: str | None = None
    total_cost_usd: float = 0.0
    notes: str = ""


# Mapping from agent identifier → constructor
_REGISTRY: dict[str, type[Agent]] = {
    AGENT_TECHNICAL: TechnicalAnalyst,
    AGENT_BTC_MACRO: BTCMacroAnalyst,
    AGENT_FUNDAMENTALS: FundamentalsAnalyst,
    AGENT_RISK: RiskManager,
    AGENT_DEVILS_ADVOCATE: DevilsAdvocate,
    AGENT_MACRO_VOICE: MacroVoice,
    AGENT_SHARIA: ShariaOfficer,
    AGENT_SYNTHESIZER: Synthesizer,
}


def _round1_agent_names(agent_set: AgentSet) -> list[str]:
    """Round 1 runs every analyst in the set EXCEPT the synthesizer."""
    return sorted(
        a for a in agent_set.agents
        if a != AGENT_SYNTHESIZER
    )


def _should_run_r2(round1: list[AgentOutput]) -> bool:
    """R2 fires when the *strongest* analyst confidence (Sharia excluded)
    sits inside the refined 0.60-0.70 band — the ambiguous middle.
    """
    confidences = [
        o.confidence for o in round1
        if o.agent_name not in (AGENT_SHARIA,)
    ]
    if not confidences:
        return False
    peak = max(confidences)
    return R2_BAND[0] <= peak <= R2_BAND[1]


# --------------------------- async core ----------------------------------

async def run_debate_async(
    agent_input: AgentInput,
    agent_set: AgentSet,
    *,
    force_full_mode: bool = False,
    skip_btc_full: bool = False,
) -> DebateResult:
    """Execute the full three-round debate.

    `force_full_mode` (used by /analyze) always runs R2 regardless of band.
    `skip_btc_full` (set by the scheduler when BTC dump protection is active)
    causes any btc_full set to drop the BTC Macro agent.
    """
    sym = agent_input.symbol.upper()
    result = DebateResult(symbol=sym, agent_set=agent_set.name)

    # --- guardrails --------------------------------------------------------
    if (agent_input.sharia_status or "").upper() == "HARAM":
        # Run the deterministic Sharia veto agent only — saves tokens
        sharia_out = ShariaOfficer().run(agent_input, round_num=1)
        result.round1 = [sharia_out]
        result.vetoed = True
        result.veto_reason = sharia_out.veto_reason or "Sharia status HARAM"
        result.notes = "Pre-debate HARAM short-circuit"
        result.total_cost_usd = sharia_out.usage.cost_usd
        return result

    # --- assemble agent set ------------------------------------------------
    r1_names = _round1_agent_names(agent_set)
    if skip_btc_full and AGENT_BTC_MACRO in r1_names:
        r1_names = [n for n in r1_names if n != AGENT_BTC_MACRO]
        result.notes = "BTC Macro skipped (BTC dump protection)"

    # --- budget check (deep call) -----------------------------------------
    allowed, reason = budget_guard.can_run_deep()
    if not allowed:
        result.notes = (result.notes + " | " + (reason or "")).strip(" |")
        result.veto_reason = reason
        return result

    # --- ROUND 1: parallel via asyncio.gather ------------------------------
    r1_agents: dict[str, Agent] = {n: _REGISTRY[n]() for n in r1_names}

    async def _run(agent_name: str, agent: Agent) -> AgentOutput:
        return await asyncio.to_thread(agent.run, agent_input, round_num=1)

    r1 = await asyncio.gather(
        *(_run(n, a) for n, a in r1_agents.items()),
        return_exceptions=False,
    )
    result.round1 = list(r1)

    # --- early Sharia veto check -----------------------------------------
    sharia_out = next((o for o in r1 if o.agent_name == AGENT_SHARIA), None)
    if sharia_out and (sharia_out.decision == "VETO"
                       or (sharia_out.structured.get("structured", {}) or {}).get("status") == "HARAM"):
        result.vetoed = True
        result.veto_reason = sharia_out.veto_reason or "Sharia veto post-Round-1"
        result.total_cost_usd = sum(o.usage.cost_usd for o in r1)
        result.notes = (result.notes + " | Sharia post-R1 veto").strip(" |")
        return result

    # --- ROUND 2: cross-critique (only when in band, or forced) -----------
    fire_r2 = force_full_mode or _should_run_r2(r1)
    if fire_r2:
        r2_agents = {n: _REGISTRY[n]() for n in r1_names if n != AGENT_SHARIA}
        # Pass the others' R1 outputs into each agent
        async def _critique(name: str, agent: Agent) -> AgentOutput:
            others = [o for o in r1 if o.agent_name != name]
            return await asyncio.to_thread(agent.run, agent_input, round_num=2, others=others)

        r2 = await asyncio.gather(
            *(_critique(n, a) for n, a in r2_agents.items()),
            return_exceptions=False,
        )
        result.round2 = list(r2)

    # --- ROUND 3: synthesizer ---------------------------------------------
    synth = Synthesizer()
    others_for_synth = result.round2 if result.round2 else result.round1
    final = await asyncio.to_thread(synth.run, agent_input, round_num=3, others=others_for_synth)
    result.final = final

    # Final veto check (synthesizer should respect Sharia + Risk-D, but we
    # double-check here to be safe)
    if sharia_out and sharia_out.decision == "VETO":
        result.vetoed = True
        result.veto_reason = sharia_out.veto_reason or "Sharia veto"

    # Cost
    result.total_cost_usd = (
        sum(o.usage.cost_usd for o in result.round1)
        + sum(o.usage.cost_usd for o in result.round2)
        + (final.usage.cost_usd if final else 0.0)
    )
    return result


def run_debate(
    agent_input: AgentInput,
    agent_set: AgentSet,
    *,
    force_full_mode: bool = False,
    skip_btc_full: bool = False,
) -> DebateResult:
    """Sync wrapper. Telegram handlers use this; the scan loop uses the async version."""
    try:
        return asyncio.run(run_debate_async(
            agent_input, agent_set,
            force_full_mode=force_full_mode,
            skip_btc_full=skip_btc_full,
        ))
    except RuntimeError:
        # Already inside an event loop (e.g. when called from a thread that
        # already has a loop). Run a fresh loop in a background thread.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, run_debate_async(
                agent_input, agent_set,
                force_full_mode=force_full_mode,
                skip_btc_full=skip_btc_full,
            ))
            return future.result()
