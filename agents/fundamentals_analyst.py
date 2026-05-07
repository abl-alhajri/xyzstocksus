"""Fundamentals Analyst — balance sheet, insiders, near-term catalysts, news."""
from __future__ import annotations

from agents.base import Agent
from config.agent_sets import AGENT_FUNDAMENTALS


class FundamentalsAnalyst(Agent):
    name = AGENT_FUNDAMENTALS
    prompt_file = "fundamentals.txt"
    max_tokens = 700

    def task_instruction(self) -> str:
        return (
            "Read sharia_ratios, insider_cluster, upcoming_events, and "
            "news_recent. Decide if the fundamental backdrop supports a long "
            "entry. Pay attention to earnings_blackout (auto-PASS) and "
            "qualifying officer-led insider clusters (bullish)."
        )
