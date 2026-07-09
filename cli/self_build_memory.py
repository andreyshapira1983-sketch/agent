"""Backward-compatible re-export of the self-build memory helper.

The implementation now lives in :mod:`core.self_build_memory` so the autonomous
runtime (a ``core`` module) can journal its own self-build proposals without a
``cli`` -> ``core`` import inversion. This shim keeps existing
``from cli.self_build_memory import ...`` call sites and tests working.
"""
from __future__ import annotations

from core.self_build_memory import (
    build_self_build_episode,
    record_self_build_episode,
)

__all__ = ["build_self_build_episode", "record_self_build_episode"]
