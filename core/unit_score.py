"""Unit-interval scores: source trust and claim confidence.

One rule shared by the source registry, knowledge pipeline and conflict review.
"""
from __future__ import annotations


def clamp_unit(value: float) -> float:
    """Number in [0.0, 1.0]; anything not readable as a number is 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))
