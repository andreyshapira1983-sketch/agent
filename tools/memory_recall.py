"""The agent's own read door into durable memory: a bounded, low-trust search.

Authority change 2026-09-04 (operator, via Кодекс: «чтение памяти как
low-trust явное извлечение… А так тебя устраивает? Да»). The write door
(`memory_bank`) opened the same day. Without an explicit read, the exam
STORE → RETRIEVE → USE cannot be sat: passive injection (`<long_term_memory>`,
three records keyed on the question) is the loop's choice, not his.

Walls: at most 5 records per call, active store only (the archive is a
separate authority, MIR-138/156), substring match on content, current
before superseded (those are marked «замещено»), then newest first,
every record wrapped with its provenance and the words LOW-TRUST — a memory
is a claim he once made, not a fact about the world now. Read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.base import Tool

MAX_RECORDS = 5


def _superseded_by(record: Any) -> str | None:
    """Id of the newer record that replaced this one, or None."""
    value = getattr(record, "superseded_by", None)
    return str(value) if value else None


def _instant(record: Any) -> float:
    """created_at as a UTC timestamp; naive = UTC, unreadable = oldest."""
    value = getattr(record, "created_at", None)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return float("-inf")
    if not isinstance(value, datetime):
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class MemoryRecallTool(Tool):
    name = "memory_recall"
    description = (
        "Search your own durable memory for records whose content contains a "
        "term. Returns at most 5, newest first, each marked LOW-TRUST with its "
        "provenance: a memory is something you once concluded, not a fact about "
        "the world now — verify before you build on it."
    )
    arguments = "term (str: a word or phrase to look for in memory content). Required."
    risk = "read_only"

    def __init__(self, *, store: Any):
        self._store = store

    def run(self, **kwargs):
        extra = set(kwargs) - {"term"}
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        if "term" not in kwargs:
            raise ValueError("memory_recall requires ['term']; " + self.arguments)
        needle = str(kwargs["term"] or "").strip().casefold()
        if not needle:
            return {"term": kwargs["term"], "records": [], "count": 0, "trust": "low"}
        try:
            records = self._store.load()
        except Exception as exc:  # noqa: BLE001 — an unreadable store is «unknown», never «empty memory»
            return {"term": kwargs["term"], "records": [], "count": 0, "trust": "low",
                    "unavailable": f"{type(exc).__name__}: {str(exc)[:120]}"}
        hits = [r for r in records if needle in str(getattr(r, "content", "")).casefold()]
        # Current records first, then newest by real time: замещённая запись
        # выглядела текущей, а str(created_at) путал пояса (проверка 17.11, средняя).
        hits.sort(key=lambda r: (_superseded_by(r) is None, _instant(r)), reverse=True)
        out = []
        for r in hits[:MAX_RECORDS]:
            newer = _superseded_by(r)
            trust = "LOW-TRUST: your own past conclusion; verify before use"
            if newer is not None:
                trust = (f"LOW-TRUST, замещено: {newer} — stale, the newer record is "
                         "current; do not build on this one")
            out.append({
                "id": getattr(r, "id", ""),
                "kind": getattr(r, "type", ""),
                "created_at": str(getattr(r, "created_at", ""))[:19],
                "source": getattr(r, "source", None),
                "content": str(getattr(r, "content", ""))[:400],
                "superseded_by": newer,
                "trust": trust,
            })
        return {
            "term": kwargs["term"], "records": out, "count": len(out),
            "more": max(0, len(hits) - MAX_RECORDS), "trust": "low",
        }
