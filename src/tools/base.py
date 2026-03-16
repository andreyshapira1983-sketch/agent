"""
Base for tools: name, description, parameters (OpenAI function-calling format).
"""
from __future__ import annotations

from typing import Any

# Tool = callable with kwargs; schema for OpenAI function calling
def tool_schema(
    name: str,
    description: str,
    parameters: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    params = parameters or {}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required or list(params.keys()),
            },
        },
    }
