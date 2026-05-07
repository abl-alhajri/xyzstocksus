"""Risk Manager — stop placement, position sizing, drawdown risk."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_RISK


class RiskManager(Agent):
    name = AGENT_RISK
    prompt_file = "risk.txt"
    max_tokens = 600

    def task_instruction(self) -> str:
        return (
            "Set stop placement (ATR-based), position size %, and a risk "
            "grade A-D. You do not pick direction — only HOLD or PASS, with "
            "the structured sizing block."
        )
