"""
Router: direct incoming request to Core/Planning/Tasks. MVP: always to Core.
"""
from __future__ import annotations

from typing import Literal

def route(user_id: str, text: str) -> Literal["core", "planning", "tasks"]:
    return "core"
