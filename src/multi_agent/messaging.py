"""
Messaging between agents. MVP: placeholder.
"""
from __future__ import annotations

from typing import Any


def send(to_role: str, message: dict[str, Any]) -> None:
    pass


def receive(role: str) -> list[dict[str, Any]]:
    return []
