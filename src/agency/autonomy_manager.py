"""
Autonomy manager: when to act alone vs ask user. MVP: always autonomous.
"""
from __future__ import annotations


def needs_confirmation(action: str) -> bool:
    return False
