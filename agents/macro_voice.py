"""Macro Voice — Powell / FOMC / Trump-attuned macro context."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_MACRO_VOICE


class MacroVoice(Agent):
    name = AGENT_MACRO_VOICE
    prompt_file = "macro_voice.txt"
    max_tokens = 600

    def task_instruction(self) -> str:
        return (
            "Decide whether the macro backdrop (Powell, FOMC, Trump, upcoming "
            "events) is a tailwind / headwind / neutral for this stock. Cite "
            "macro_recent entries by index."
        )
