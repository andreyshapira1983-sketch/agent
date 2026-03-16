"""
Re-export AutoPatch из src.evolution для удобства импорта из tools.
Реализация и безопасный поток (sandbox): src/evolution/auto_patch.py, src/evolution/safety.py.
"""
from __future__ import annotations

from src.evolution.auto_patch import AutoPatch

__all__ = ["AutoPatch"]
