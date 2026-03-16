"""
Communication interface: receive user message, return reply (channel-agnostic).
"""
from __future__ import annotations

from typing import Callable, Awaitable

# Type: (user_id, text) -> reply text (async)
MessageHandler = Callable[[str, str], Awaitable[str]]


async def handle_incoming(
    user_id: str,
    text: str,
    handler: MessageHandler,
) -> str:
    """Call handler and return reply (or error message)."""
    try:
        return await handler(user_id, text)
    except Exception as e:
        return f"Ошибка: {e}"
