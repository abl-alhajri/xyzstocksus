"""Abstract agent base — every analyst agent inherits from `Agent`.

Each agent is a thin wrapper that:
1. Reads its system prompt from llm/prompts/<name>.txt (cached at the LLM layer).
2. Builds a structured user payload from `AgentInput`.
3. Calls llm.client.complete() with the right model.
4. Records cost via core.cost_tracker.
5. Returns an AgentOutput with parsed JSON + usage.

The orchestrator runs Round 1 agents in parallel via asyncio.gather. Each
agent.complete() is sync but cheap to wrap with asyncio.to_thread.
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import settings
from core import cost_tracker
from core.logger import get_logger
from llm.client import LLMResponse, LLMUsage, complete

log = get_logger("agents.base")

PROMPTS_DIR = Path(__file__).parent.parent / "llm" / "prompts"


@dataclass
class AgentInput:
    """Bundle of structured facts available to every agent.

    All numeric fields are pre-computed by the data + indicator layer. Agents
    are explicitly forbidden (per system prompt) from inventing numbers, so
    everything they need to cite must live in this object.
    """
    symbol: str
    sector: str
    agent_set: str
    sharia_status: str
    last_price: float | None
    heuristic: dict
    technical: dict | None
    btc_price: float | None
    btc_regime: str | None
    btc_corr_30d: float | None
    btc_beta: float
    macro_recent: list[dict] = field(default_factory=list)
    upcoming_events: list[dict] = field(default_factory=list)
    earnings_blackout: bool = False
    insider_cluster: dict | None = None
    sharia_ratios: dict | None = None
    news_recent: list[dict] = field(default_factory=list)
    network_stats: dict | None = None
    extras: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "agent_set": self.agent_set,
            "sharia_status": self.sharia_status,
            "last_price": self.last_price,
            "heuristic": self.heuristic,
            "technical": self.technical,
            "btc_context": {
                "price": self.btc_price,
                "regime": self.btc_regime,
                "corr_30d": self.btc_corr_30d,
                "beta": self.btc_beta,
            },
            "macro_recent": self.macro_recent,
            "upcoming_events": self.upcoming_events,
            "earnings_blackout": self.earnings_blackout,
            "insider_cluster": self.insider_cluster,
            "sharia_ratios": self.sharia_ratios,
            "news_recent": self.news_recent,
            "network_stats": self.network_stats,
            "extras": self.extras,
        }


@dataclass
class AgentOutput:
    agent_name: str
    decision: str               # BUY | HOLD | PASS | VETO (sharia)
    confidence: float
    rationale: str
    structured: dict            # full parsed JSON from the LLM
    usage: LLMUsage
    raw_text: str
    veto_reason: str | None = None


class Agent(abc.ABC):
    """Concrete agents subclass this and provide name + prompt_file."""

    name: str = "base"
    prompt_file: str = "system_base.txt"
    extra_prompt_file: str | None = None
    model: str = ""             # filled in by subclass (haiku / sonnet)
    max_tokens: int = 700
    temperature: float = 0.2

    def __init__(self):
        if not self.model:
            self.model = settings.model_sonnet

    def system_parts(self) -> list[tuple[str, bool]]:
        base = (PROMPTS_DIR / "system_base.txt").read_text(encoding="utf-8")
        own = ""
        if self.prompt_file != "system_base.txt":
            own = (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8")
        # Both blocks cached so 8 parallel agents share the cache hit
        parts = [(base, True), (own, True)]
        if self.extra_prompt_file:
            parts.append(((PROMPTS_DIR / self.extra_prompt_file).read_text(encoding="utf-8"), True))
        return parts

    def user_messages(self, agent_input: AgentInput, *,
                      round_num: int,
                      others: list[AgentOutput] | None = None) -> list[dict]:
        payload: dict[str, Any] = {
            "round": round_num,
            "agent": self.name,
            "task": self.task_instruction(),
            "data": agent_input.to_payload(),
            "schema": self.output_schema(),
        }
        if others:
            payload["round_1_outputs"] = [
                {
                    "agent": o.agent_name,
                    "decision": o.decision,
                    "confidence": o.confidence,
                    "rationale": o.rationale[:600],
                }
                for o in others
            ]
        return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    @abc.abstractmethod
    def task_instruction(self) -> str:
        """A 1-3 sentence summary of what the agent must produce."""

    def output_schema(self) -> dict:
        """JSON schema sketch. Subclasses can extend `structured`."""
        return {
            "decision": "BUY | HOLD | PASS",
            "confidence": "float in [0,1]",
            "rationale": "1-3 sentences citing structured.data fields",
            "structured": "object — agent-specific fields",
        }

    def run(self, agent_input: AgentInput, *,
            round_num: int = 1,
            others: list[AgentOutput] | None = None) -> AgentOutput:
        try:
            response = complete(
                model=self.model,
                user_messages=self.user_messages(agent_input, round_num=round_num, others=others),
                system_parts=self.system_parts(),
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                parse_json=True,
            )
        except Exception as exc:
            log.warning("agent call failed",
                        extra={"agent": self.name, "symbol": agent_input.symbol,
                               "err": str(exc)})
            return AgentOutput(
                agent_name=self.name,
                decision="HOLD",
                confidence=0.0,
                rationale=f"Agent unavailable: {exc}",
                structured={},
                usage=LLMUsage(model=self.model),
                raw_text="",
            )

        cost_tracker.record_call(response.usage, agent=self.name, symbol=agent_input.symbol)
        parsed = response.parsed_json or {}
        decision = str(parsed.get("decision", "HOLD")).upper()
        try:
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        rationale = str(parsed.get("rationale") or "")[:1200]
        veto_reason = parsed.get("veto_reason") if isinstance(parsed, dict) else None

        return AgentOutput(
            agent_name=self.name,
            decision=decision,
            confidence=confidence,
            rationale=rationale,
            structured=parsed,
            usage=response.usage,
            raw_text=response.text,
            veto_reason=veto_reason,
        )
