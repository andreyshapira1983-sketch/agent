"""
Self-learning: improve from dialogue and feedback. MVP: log only.
"""
from __future__ import annotations

from src.learning.feedback import add_feedback


def record_exchange(request: str, response: str) -> None:
    add_feedback(request, response)
