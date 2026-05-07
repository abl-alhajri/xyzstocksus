"""Synthesizer — final unified decision."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_SYNTHESIZER


class Synthesizer(Agent):
    name = AGENT_SYNTHESIZER
    prompt_file = "synthesizer.txt"
    max_tokens = 900

    def task_instruction(self) -> str:
        return (
            "Combine every other agent's round 1 (and round 2 if present) "
            "outputs into a single unified decision with entry zone, stop "
            "loss, three take-profits, and a final confidence. Hard-PASS if "
            "Sharia agent vetoes or Risk grade is D."
        )
