"""
Model selector: price/quality. MVP: return default.
"""
from __future__ import annotations


def get_model(fast: bool = True) -> str:
    return "gpt-4o-mini" if fast else "gpt-4o"
