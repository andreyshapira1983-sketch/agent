"""
Context interpreter: user intent, facts. MVP: pass through.
"""
from __future__ import annotations

from typing import Any


def interpret(parsed: dict[str, Any]) -> dict[str, Any]:
    return {"intent": "chat", "content": parsed.get("text", "")}
