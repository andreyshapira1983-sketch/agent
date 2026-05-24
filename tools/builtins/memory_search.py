"""
tools/builtins/memory_search.py — Search agent memory

Brain uses this to explicitly retrieve past context
without building a full ContextBuilder cycle.

Useful when:
    - Brain needs to recall something from a different session
    - Brain wants to search by keyword across stored facts
"""

from __future__ import annotations

from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec
from brain.interfaces.memory_interface import MemoryInterface


class MemorySearchTool(ToolBase):
    """
    Search stored memory for entries matching a query keyword.

    params:
        session_id (str): Which session to search.
        query      (str): Keyword/phrase to search for.
        limit      (int, optional): Max results (default 10).
    """

    def __init__(self, memory: MemoryInterface) -> None:
        self._memory = memory

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_search",
            description="Search stored conversation memory for entries matching a query",
            parameters={
                "session_id": "str — session to search",
                "query":      "str — keyword or phrase",
                "limit":      "int (optional, default 10) — max results to return",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        session_id: str = params.get("session_id", "")
        query: str = params.get("query", "")
        limit: int = int(params.get("limit", 10))

        if not session_id:
            return self._fail("'session_id' param is required")
        if not query:
            return self._fail("'query' param is required")
        if limit < 1 or limit > 100:
            return self._fail("'limit' must be between 1 and 100")

        query_lower = query.lower()

        try:
            history = self._memory.retrieve(session_id)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"Memory read error: {exc}")

        matches = [
            entry for entry in history
            if query_lower in str(entry).lower()
        ][:limit]

        return self._ok(
            output=matches,
            total_matches=len(matches),
            session_id=session_id,
            query=query,
        )
