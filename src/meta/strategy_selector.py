"""
Strategy selector: which strategy for task. MVP: default.
"""
from __future__ import annotations


def select_strategy(query: str) -> str:
    return "direct"
