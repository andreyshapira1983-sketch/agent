"""
Re-export VectorMemory из src.memory для удобства импорта из tools.
Реализация: src/memory/vector_memory.py.
"""
from __future__ import annotations

from src.memory.vector_memory import VectorMemory

__all__ = ["VectorMemory"]
