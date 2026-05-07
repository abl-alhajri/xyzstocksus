"""Devil's Advocate — tries to kill every BUY with the strongest counter."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_DEVILS_ADVOCATE


class DevilsAdvocate(Agent):
    name = AGENT_DEVILS_ADVOCATE
    prompt_file = "devils_advocate.txt"
    max_tokens = 700
    temperature = 0.4   # a touch more creative — looking for the kill

    def task_instruction(self) -> str:
        return (
            "Produce the single most damaging counter-thesis to a long entry "
            "in this stock. Cite exact fields. Mandatory for non-lean sets — "
            "you must always return a kill_thesis even if your confidence is low."
        )
