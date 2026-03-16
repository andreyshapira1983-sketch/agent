"""
Simple tool: current time (for testing Tool System + Execution).
"""
from datetime import datetime, timezone

from src.tools.base import tool_schema
from src.tools.registry import register


def _get_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_time_tool() -> None:
    schema = tool_schema(
        "get_current_time",
        "Returns current UTC time in ISO format.",
        {},
    )
    register("get_current_time", schema, lambda: _get_time())
