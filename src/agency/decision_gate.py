"""
Decision gate: check Security, Ethics, limits before execution. MVP: allow all.
"""
from __future__ import annotations

from typing import Any


def allow_decision(decision: dict[str, Any]) -> bool:
    return True
