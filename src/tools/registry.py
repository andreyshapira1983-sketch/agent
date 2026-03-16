"""
Tool registry: register and get tools by name; list for OpenAI.
"""
from __future__ import annotations

from typing import Any, Callable

_registry: dict[str, tuple[dict, Callable[..., Any]]] = {}  # name -> (schema, fn)


def register(name: str, schema: dict[str, Any], fn: Callable[..., Any]) -> None:
    _registry[name] = (schema, fn)


def get(name: str) -> tuple[dict[str, Any], Callable[..., Any]] | None:
    return _registry.get(name)


def list_tools() -> list[dict[str, Any]]:
    return [s for s, _ in _registry.values()]


def call(name: str, **kwargs: Any) -> Any:
    entry = _registry.get(name)
    if not entry:
        raise KeyError(f"Unknown tool: {name}")
    _, fn = entry
    return fn(**kwargs)
