"""
Context manager: switch between working memory and episodic; task switching rules.
MVP: delegate to short_term for current dialogue context.
"""
from __future__ import annotations

from src.memory import short_term


def get_context_for_llm(user_id: str) -> list[dict[str, str]]:
    """Return messages suitable for LLM context (working memory)."""
    return short_term.get_messages(user_id)
