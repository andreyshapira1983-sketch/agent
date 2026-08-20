"""Step repetition detector — addresses MAST FM-1.3 (step repetition, 15.7%)."""

from __future__ import annotations

import json
from typing import Any

_DEFAULT_REPEAT_THRESHOLD = 3


def normalize_args(arguments: Any) -> str:
    """Stable JSON form of arguments for hashing across attempts."""
    if arguments is None:
        return ""
    try:
        return json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(arguments)


class StepRepetitionTracker:
    """Per-run counter of executed (tool, args) pairs."""

    def __init__(self, threshold: int = _DEFAULT_REPEAT_THRESHOLD) -> None:
        self.threshold = max(2, int(threshold))
        self._counts: dict[tuple[str, str], int] = {}
        self._reported: set[tuple[str, str]] = set()

    def observe(self, tool: str, arguments: Any) -> dict[str, Any] | None:
        """Record one (tool, args) execution."""
        if not tool:
            return None
        key = (tool, normalize_args(arguments))
        self._counts[key] = self._counts.get(key, 0) + 1
        count = self._counts[key]
        if count >= self.threshold and key not in self._reported:
            self._reported.add(key)
            return {
                "tool": tool,
                "arguments_signature": key[1][:300],
                "repetitions": count,
                "threshold": self.threshold,
            }
        return None

    def count(self, tool: str, arguments: Any) -> int:
        return self._counts.get((tool, normalize_args(arguments)), 0)

    def summary(self) -> dict[str, int]:
        """All distinct (tool, args) pairs and their counts. Diagnostic only."""
        return {f"{t}|{a[:80]}": c for (t, a), c in self._counts.items()}
