"""Pre-flight token estimate so the budget guard can refuse before we burn $.

Uses a 4-chars-per-token rule of thumb. Good enough for budget gating; the
real usage is recorded after the call.
"""
from __future__ import annotations


def rough_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_call_tokens(*, system_text: str = "", user_text: str = "",
                         expected_output_tokens: int = 600) -> dict:
    return {
        "input_tokens": rough_tokens(system_text) + rough_tokens(user_text),
        "output_tokens": expected_output_tokens,
    }
