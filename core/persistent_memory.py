"""Persistent Memory Record store (§4 — long-term, JSONL on disk).

Minimal contract:
  - append-only writes (one MemoryRecord per JSONL line)
  - whole-file load on demand
  - per-id delete + bulk delete
  - corrupted lines are skipped, not fatal
  - low-value records are ARCHIVED (not deleted) — see archive_record()
  - expired records (ttl_seconds set and past) are evicted on load()
"""
from __future__ import annotations

import datetime as _dt
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
    """JSONL-backed list of MemoryRecords.

    Two files on disk:
      self.path         — active memory (high-value, frequently accessed)
      self.archive_path — archived memory (low-value, moved out of hot path)

    Active memory is loaded every cycle. Archive is never injected into
    prompts automatically — it is a reference store for explicit recall.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Archive lives next to the active store with a .archive suffix
        self.archive_path = self.path.with_suffix(".archive.jsonl")

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

    def update(self, record: MemoryRecord) -> bool:
        """Replace an existing record in-place (full rewrite). Returns True on success."""
        records = self.load()
        updated = False
        new_records = []
        for r in records:
            if r.id == record.id:
                new_records.append(record)
                updated = True
            else:
                new_records.append(r)
        if updated:
            self._rewrite(new_records)
        return updated

    def archive_record(self, record_id: str) -> bool:
        """Move a record from active store to archive. Returns True if moved."""
        records = self.load()
        target = next((r for r in records if r.id == record_id), None)
        if target is None:
            return False
        # Mark as archived and append to archive store
        target = target.model_copy(update={"archived": True})
        with state_file_lock(self.archive_path):
            append_state_jsonl_unlocked(self.archive_path, [target.model_dump(mode="json")])
        # Remove from active store
        keep = [r for r in records if r.id != record_id]
        self._rewrite(keep)
        return True

    # ---------- reads ----------

    def load(self) -> list[MemoryRecord]:
        """Full file scan — returns only LIVE (non-expired) records.

        TTL eviction: records with ``ttl_seconds`` set are considered expired
        when ``created_at + ttl_seconds <= now(UTC)``.  Expired records are
        silently dropped from the return value **and** removed from the on-disk
        store so they don't accumulate across restarts.
        """
        all_records = self._load_raw()
        now = _dt.datetime.now(_dt.timezone.utc)
        live: list[MemoryRecord] = []
        expired_found = False
        for rec in all_records:
            if rec.ttl_seconds is not None:
                expires_at = rec.created_at + _dt.timedelta(seconds=rec.ttl_seconds)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
                if expires_at <= now:
                    expired_found = True
                    continue
            live.append(rec)
        # Lazy eviction: rewrite store only when expired records are found
        if expired_found:
            self._rewrite(live)
        return live

    def _load_raw(self) -> list[MemoryRecord]:
        """Load ALL records from disk without TTL filtering.

        Used by :func:`core.hygiene.expire_memory` so it can read and
        report expired records before deleting them.  Normal callers
        should use :meth:`load` instead.
        """
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        for row in rows:
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def load_archive(self) -> list[MemoryRecord]:
        """Load archived records (for explicit recall/inspection only)."""
        if not self.archive_path.exists():
            return []
        out: list[MemoryRecord] = []
        with state_file_lock(self.archive_path):
            rows = read_state_jsonl_unlocked(self.archive_path)
        for row in rows:
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def count(self) -> int:
        return len(self.load())

    def count_archive(self) -> int:
        return len(self.load_archive())

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
