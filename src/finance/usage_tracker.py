"""
Usage tracker: count tokens and API calls.
"""
from __future__ import annotations

_total_tokens = 0
_total_calls = 0


def add_usage(tokens: int = 0, calls: int = 1) -> None:
    global _total_tokens, _total_calls
    _total_tokens += tokens
    _total_calls += calls


def get_usage() -> tuple[int, int]:
    return _total_tokens, _total_calls
