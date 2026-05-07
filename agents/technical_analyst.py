"""Technical Analyst agent — chart structure, momentum, volume."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_TECHNICAL


class TechnicalAnalyst(Agent):
    name = AGENT_TECHNICAL
    prompt_file = "technical.txt"
    max_tokens = 700

    def task_instruction(self) -> str:
        return (
            "Analyse the chart structure (trend, momentum, volume) using ONLY the "
            "fields in `data.technical` and `data.heuristic`. Recommend BUY / HOLD "
            "/ PASS with confidence and a suggested stop in ATR multiples."
        )
