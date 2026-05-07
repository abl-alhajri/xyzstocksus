"""Sharia Compliance Officer — VETO power on every signal.

The agent itself only renders the verdict. The actual ratio numbers come from
the deterministic sharia.verifier and are passed in via AgentInput.sharia_ratios
+ AgentInput.sharia_status, so the LLM never produces a number used for
compliance.
"""
from __future__ import annotations

from agents.base import Agent, AgentInput, AgentOutput
from config.agent_sets import AGENT_SHARIA


class ShariaOfficer(Agent):
    name = AGENT_SHARIA
    prompt_file = "sharia.txt"
    max_tokens = 600
    temperature = 0.1   # rendering deterministic facts — keep deterministic

    def task_instruction(self) -> str:
        return (
            "Render a Sharia verdict using ONLY data.sharia_status and "
            "data.sharia_ratios. You have VETO power: decision=VETO when "
            "status is HARAM or any tier is RED. Always include the Arabic "
            "summary in structured.summary_arabic."
        )

    def run(self, agent_input: AgentInput, *, round_num: int = 1,
            others=None) -> AgentOutput:
        # Fast path: when input clearly says HARAM, skip the LLM entirely.
        # Saves tokens on the deterministic veto cases (which are common).
        if (agent_input.sharia_status or "").upper() == "HARAM":
            return AgentOutput(
                agent_name=self.name,
                decision="VETO",
                confidence=1.0,
                rationale="Stock is HARAM per AAOIFI screen — auto-veto.",
                structured={
                    "decision": "VETO",
                    "confidence": 1.0,
                    "rationale": "Stock is HARAM per AAOIFI screen — auto-veto.",
                    "veto_reason": "Sharia status HARAM",
                    "structured": {
                        "status": "HARAM",
                        "drift_warning": bool(
                            (agent_input.sharia_ratios or {}).get("drift_warning")
                        ),
                        "summary_arabic": "🔴 غير شرعي",
                        "as_of_filing": (agent_input.sharia_ratios or {}).get("filing_date"),
                    },
                },
                usage=_zero_usage(),
                raw_text="(deterministic veto — LLM skipped)",
                veto_reason="Sharia status HARAM",
            )
        return super().run(agent_input, round_num=round_num, others=others)


def _zero_usage():
    from llm.client import LLMUsage
    return LLMUsage(model="deterministic")
