"""
Feedback loop: use feedback for learning. MVP: placeholder.
"""
from __future__ import annotations

from src.learning.feedback import get_recent_feedback


def process_feedback() -> int:
    return len(get_recent_feedback(100))
