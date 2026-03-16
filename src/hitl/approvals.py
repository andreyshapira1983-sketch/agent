"""
Approvals: request human confirm. MVP: auto-approve.
"""
from __future__ import annotations

from typing import Any


async def request_approval(action: str, params: dict[str, Any]) -> bool:
    return True
