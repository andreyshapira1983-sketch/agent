"""Persistent Memory Record store (§4 — long-term, JSONL on disk).

Minimal contract:
  - append-only writes (one MemoryRecord per JSONL line)
  - whole-file load on demand
  - per-id delete + bulk delete
  - corrupted lines are skipped, not fatal

Explicitly NOT in MVP-5: SQLite, embeddings, vector index, RAG, summarisation,
TTL eviction. The store is dumb — the brain is the WritePolicy + RetrievalPolicy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.models import MemoryRecord
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)


class PersistentMemoryStore:
    """JSONL-backed list of MemoryRecords."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- writes ----------

    def save(self, record: MemoryRecord) -> None:
        """Append one record. O(1) write."""
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [record.model_dump(mode="json")])

    def save_many(self, records: Iterable[MemoryRecord]) -> int:
        payloads: list[dict] = []
        for r in records:
            payloads.append(r.model_dump(mode="json"))
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, payloads)
        return len(payloads)

    # ---------- reads ----------

    def load(self) -> list[MemoryRecord]:
        """Full file scan. Corrupted lines are skipped."""
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        for row in rows:
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                # Skip silently — corrupted semantic records should not bring
                # the session down. The generic JSON/checksum layer has already
                # quarantined syntactically corrupt or tampered rows.
                continue
        return out

    def count(self) -> int:
        return len(self.load())

    def get(self, record_id: str) -> MemoryRecord | None:
        for r in self.load():
            if r.id == record_id:
                return r
        return None

    # ---------- deletes ----------

    def delete(self, record_id: str) -> bool:
        """Returns True if a record was actually removed."""
        records = self.load()
        keep = [r for r in records if r.id != record_id]
        if len(keep) == len(records):
            return False
        self._rewrite(keep)
        return True

    def delete_all(self) -> int:
        n = self.count()
        with state_file_lock(self.path):
            if self.path.exists():
                self.path.unlink()
        return n

    # ---------- helpers ----------

    def _rewrite(self, records: list[MemoryRecord]) -> None:
        with state_file_lock(self.path):
            rewrite_state_jsonl_unlocked(
                self.path,
                [record.model_dump(mode="json") for record in records],
            )
