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

import json
from pathlib import Path
from typing import Iterable

from core.models import MemoryRecord


class PersistentMemoryStore:
    """JSONL-backed list of MemoryRecords."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- writes ----------

    def save(self, record: MemoryRecord) -> None:
        """Append one record. O(1) write."""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def save_many(self, records: Iterable[MemoryRecord]) -> int:
        n = 0
        with self.path.open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(r.model_dump_json() + "\n")
                n += 1
        return n

    # ---------- reads ----------

    def load(self) -> list[MemoryRecord]:
        """Full file scan. Corrupted lines are skipped."""
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(MemoryRecord.model_validate_json(line))
                except (json.JSONDecodeError, ValueError):
                    # Skip silently — corrupted line should not bring the
                    # session down. A future MVP can quarantine these.
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
        if self.path.exists():
            self.path.unlink()
        return n

    # ---------- helpers ----------

    def _rewrite(self, records: list[MemoryRecord]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(r.model_dump_json() + "\n")
        tmp.replace(self.path)
