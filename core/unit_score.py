"""Оценка в долях единицы: доверие к источнику, уверенность в утверждении.

Реестр источников (`core/source_registry.py`), конвейер знаний
(`core/knowledge_pipeline.py`) и разбор конфликтов (`core/conflict_review.py`)
держали по своей копии одного и того же правила; теперь оно здесь одно.
"""
from __future__ import annotations


def clamp_unit(value: float) -> float:
    """Число в [0.0, 1.0]; то, что числом не читается, — 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))
