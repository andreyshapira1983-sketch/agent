"""
Adaptation: adjust prompts/rules from feedback. MVP: placeholder.
"""
from __future__ import annotations

from src.learning.feedback import get_recent_feedback


def adapt_from_feedback() -> dict:
    data = get_recent_feedback(20)
    return {"samples": len(data)}
