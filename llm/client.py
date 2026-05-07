"""Anthropic SDK wrapper with aggressive prompt caching and cost accounting.

Why this lives in one file:
- Every agent and the Haiku pre-screen go through `complete()` so we never
  miss a cost-tracking entry.
- Prompt caching is set up here once (cache_control on system blocks +
  watchlist context) so callers don't have to think about it.
- Retries + JSON parsing live here so agents stay simple.

Cache strategy (per refined plan, target ≥90% reduction on cached input):
- All 8 agent system prompts are cached (cache_control: ephemeral)
- Per-scan watchlist context block is cached
- Per-symbol structured input is NOT cached (it changes every scan)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from config.settings import settings
from core.logger import get_logger

log = get_logger("llm.client")

# Pricing in USD per million tokens. Override via env if Anthropic adjusts.
PRICING: dict[str, dict[str, float]] = {
    # Haiku 4.5
    "claude-haiku-4-5": {
        "input": 1.00, "output": 5.00,
        "cache_read": 0.10, "cache_write": 1.25,
    },
    # Sonnet 4.6
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00,
        "cache_read": 0.30, "cache_write": 3.75,
    },
}

DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""


@dataclass
class LLMResponse:
    text: str
    parsed_json: Any | None
    usage: LLMUsage
    raw_blocks: list[dict] = field(default_factory=list)


class AnthropicUnavailable(RuntimeError):
    pass


def _client():
    """Lazy import — keeps the module importable without the SDK installed."""
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise AnthropicUnavailable("anthropic SDK not installed") from exc
    if not settings.anthropic_api_key:
        raise AnthropicUnavailable("ANTHROPIC_API_KEY missing")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _system_block(text: str, *, cache: bool) -> dict:
    """Return a system block in the new (cache-enabled) format."""
    block: dict = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _make_system(parts: Iterable[tuple[str, bool]]) -> list[dict]:
    """Build a list of system blocks from (text, cache?) tuples."""
    return [_system_block(t, cache=c) for t, c in parts if t and t.strip()]


def estimate_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Compute USD cost for a given token mix using PRICING."""
    p = PRICING.get(model, DEFAULT_PRICING)
    fresh_input = max(input_tokens - cached_tokens - cache_creation_tokens, 0)
    cost = (
        fresh_input * p["input"]
        + cached_tokens * p["cache_read"]
        + cache_creation_tokens * p["cache_write"]
        + output_tokens * p["output"]
    ) / 1_000_000.0
    return round(cost, 6)


def _extract_usage(model: str, raw_usage: Any) -> LLMUsage:
    if raw_usage is None:
        return LLMUsage(model=model)
    in_t = getattr(raw_usage, "input_tokens", None) or 0
    out_t = getattr(raw_usage, "output_tokens", None) or 0
    cached = getattr(raw_usage, "cache_read_input_tokens", None) or 0
    cache_create = getattr(raw_usage, "cache_creation_input_tokens", None) or 0
    cost = estimate_cost(
        model=model,
        input_tokens=in_t,
        output_tokens=out_t,
        cached_tokens=cached,
        cache_creation_tokens=cache_create,
    )
    return LLMUsage(
        input_tokens=in_t,
        output_tokens=out_t,
        cached_tokens=cached,
        cache_creation_tokens=cache_create,
        cost_usd=cost,
        model=model,
    )


def _coerce_text(content: Any) -> str:
    """Anthropic returns a list of content blocks; concat the text ones."""
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _parse_json_block(text: str) -> Any | None:
    """Extract the largest valid JSON object from `text`.

    Agents are instructed to emit a single JSON object — but we tolerate
    surrounding prose by scanning for ```json fences first and then for the
    first balanced { ... }.
    """
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL | re.IGNORECASE)
    candidates: list[str] = []
    if fence:
        candidates.append(fence.group(1))
    # Greedy outer-most {...}
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first:last + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def complete(
    *,
    model: str,
    user_messages: list[dict],
    system_parts: Iterable[tuple[str, bool]] = (),
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout_s: float = 60.0,
    parse_json: bool = True,
) -> LLMResponse:
    """Single Anthropic call with prompt caching, retries, and JSON parsing.

    `system_parts` is a sequence of (text, cache?) tuples — the helper turns
    them into the SDK's system blocks, attaching cache_control where requested.
    """
    started = time.monotonic()
    system = _make_system(system_parts)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            client = _client()
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system if system else None,
                messages=user_messages,
                timeout=timeout_s,
            )
            usage = _extract_usage(model, getattr(resp, "usage", None))
            usage.latency_ms = int((time.monotonic() - started) * 1000)
            text = _coerce_text(getattr(resp, "content", []))
            parsed = _parse_json_block(text) if parse_json else None
            blocks = [b if isinstance(b, dict) else getattr(b, "model_dump", lambda: {})()
                      for b in (getattr(resp, "content", []) or [])]
            return LLMResponse(text=text, parsed_json=parsed, usage=usage, raw_blocks=blocks)
        except AnthropicUnavailable:
            raise
        except Exception as exc:
            last_exc = exc
            backoff = 1.5 ** attempt
            log.warning("anthropic call failed",
                        extra={"attempt": attempt, "err": str(exc), "backoff": backoff})
            time.sleep(backoff)

    raise RuntimeError(f"anthropic call failed after retries: {last_exc}")
