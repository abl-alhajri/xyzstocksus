"""BTC Macro Analyst — only used for btc_full names."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_BTC_MACRO


class BTCMacroAnalyst(Agent):
    name = AGENT_BTC_MACRO
    prompt_file = "btc_macro.txt"
    max_tokens = 600

    def task_instruction(self) -> str:
        return (
            "Decide whether BTC's current regime is a tailwind / headwind / "
            "neutral for this stock specifically. For miners, fold in "
            "network_stats. For treasuries (MSTR-style), weight the 30d "
            "correlation. Output the structured BTC alignment block."
        )
