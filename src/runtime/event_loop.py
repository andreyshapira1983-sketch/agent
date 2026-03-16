"""
Event loop: receive events, dispatch. MVP: use asyncio.get_event_loop().
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

_handlers: list[Callable[[Any], Awaitable[None]]] = []


async def emit(event: Any) -> None:
    for h in _handlers:
        await h(event)


def on_event(handler: Callable[[Any], Awaitable[None]]) -> None:
    _handlers.append(handler)
