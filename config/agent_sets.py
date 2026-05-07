"""Agent set definitions and resolver.

Three sets:
- btc_full: 8 agents (everything, including BTC Macro)
- standard: 7 agents (BTC Macro skipped — non-BTC name)
- lean:     5 agents (Technical, Risk, Macro Voice, Sharia, Synthesizer — for ETFs)

Sharia officer is in EVERY set and has VETO power.
Devil's Advocate is mandatory for non-lean.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


# Stable agent identifiers — match agents/<name>.py module names
AGENT_TECHNICAL = "technical"
AGENT_BTC_MACRO = "btc_macro"
AGENT_FUNDAMENTALS = "fundamentals"
AGENT_RISK = "risk"
AGENT_DEVILS_ADVOCATE = "devils_advocate"
AGENT_MACRO_VOICE = "macro_voice"
AGENT_SHARIA = "sharia"
AGENT_SYNTHESIZER = "synthesizer"

ALL_AGENTS: FrozenSet[str] = frozenset({
    AGENT_TECHNICAL,
    AGENT_BTC_MACRO,
    AGENT_FUNDAMENTALS,
    AGENT_RISK,
    AGENT_DEVILS_ADVOCATE,
    AGENT_MACRO_VOICE,
    AGENT_SHARIA,
    AGENT_SYNTHESIZER,
})


@dataclass(frozen=True)
class AgentSet:
    name: str
    agents: FrozenSet[str]
    description: str

    def includes(self, agent: str) -> bool:
        return agent in self.agents


BTC_FULL = AgentSet(
    name="btc_full",
    agents=frozenset(ALL_AGENTS),
    description="BTC-correlated names: full 8-agent debate including BTC Macro",
)

STANDARD = AgentSet(
    name="standard",
    agents=ALL_AGENTS - {AGENT_BTC_MACRO},
    description="Regular equities: 7 agents (skip BTC Macro)",
)

LEAN = AgentSet(
    name="lean",
    agents=frozenset({
        AGENT_TECHNICAL,
        AGENT_RISK,
        AGENT_MACRO_VOICE,
        AGENT_SHARIA,
        AGENT_SYNTHESIZER,
    }),
    description="ETFs: lean 5-agent debate (no Fundamentals, no Devil's Advocate, no BTC Macro)",
)


SETS_BY_NAME: dict[str, AgentSet] = {
    BTC_FULL.name: BTC_FULL,
    STANDARD.name: STANDARD,
    LEAN.name: LEAN,
}


# Sector → agent set mapping. Sharia officer always runs regardless.
_BTC_SECTORS = frozenset({
    "BTC_TREASURY",
    "BTC_MINER",
    "CRYPTO_EXCHANGE",
    "CRYPTO_ADJACENT",
    "MINING_HARDWARE",
})

_ETF_SECTORS = frozenset({"HALAL_ETF", "HALAL_SUKUK"})


def resolve_set_for_sector(sector: str) -> AgentSet:
    """Map a sector to its default agent set.

    Used at scan time to pick the right debate composition per ticker.
    """
    sector = (sector or "").upper()
    if sector in _ETF_SECTORS:
        return LEAN
    if sector in _BTC_SECTORS:
        return BTC_FULL
    return STANDARD
