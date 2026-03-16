"""
Limits: budget, max tokens/calls. MVP: configurable caps.
"""
from __future__ import annotations

MAX_TOKENS_PER_DAY = 100_000
MAX_CALLS_PER_DAY = 500

_tokens_today = 0
_calls_today = 0


def check_limit(tokens: int, calls: int = 1) -> bool:
    global _tokens_today, _calls_today
    if _tokens_today + tokens > MAX_TOKENS_PER_DAY:
        return False
    if _calls_today + calls > MAX_CALLS_PER_DAY:
        return False
    return True


def consume(tokens: int, calls: int = 1) -> None:
    global _tokens_today, _calls_today
    _tokens_today += tokens
    _calls_today += calls
