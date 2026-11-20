"""Persistent Memory Record store: long-term JSONL on disk.

    Append-only writes (one MemoryRecord per line), whole-file load on demand,
    per-id and bulk delete, corrupted lines skipped, low-value records archived
    (not deleted), expired records (ttl_seconds past) evicted on load()."""
from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The agent's own write door (its vocabulary, its refusals) is in core/memory_door.py.
# Names are re-exported: the memory_bank tool and tests import them from here.
from core.memory_door import (  # noqa: F401
    ACCEPTED_KINDS_TEXT,
    CONSENT_TAG_MAP,
    _looks_like_code,
    memory_door_verdict,
    memory_door_write,
)
from core.models import MemoryRecord
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)


def record_is_expired(rec: MemoryRecord, now: _dt.datetime) -> bool:
    """The one TTL rule for the store AND hygiene: ttl<=0 is already expired; a TTL reaching past the calendar never expires.

        `timedelta` overflowed on a huge TTL and one such row made `load()` raise."""
    if rec.ttl_seconds is None:
        return False
    try:
        expires_at = rec.created_at + _dt.timedelta(seconds=rec.ttl_seconds)
    except (OverflowError, ValueError):
        return rec.ttl_seconds <= 0
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
    return expires_at <= now


class PersistentMemoryStore:
    """JSONL-backed list of MemoryRecords.

        Active memory is loaded every cycle. Archive is never injected into prompts automatically; it is for explicit recall."""

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
        with state_file_lock(self.path):
            records = self._active_unlocked()
            new_records = []
            updated = False
            for r in records:
                if r.id == record.id:
                    new_records.append(record)
                    updated = True
                else:
                    new_records.append(r)
            if updated:
                self._rewrite_unlocked(new_records)
        return updated

    def touch_access(self, ids: Iterable[str], at: _dt.datetime) -> int:
        """Bump access stats on the CURRENT records, under one lock.

            Only the two access fields move, so a concurrent `superseded_by` is not erased."""
        wanted = set(ids)
        if not wanted:
            return 0
        with state_file_lock(self.path):
            current = self._active_unlocked()
            touched = 0
            merged: list[MemoryRecord] = []
            for rec in current:
                if rec.id in wanted:
                    rec = rec.model_copy(update={
                        "access_count": rec.access_count + 1,
                        "last_accessed_at": at,
                    })
                    touched += 1
                merged.append(rec)
            if touched:
                self._rewrite_unlocked(merged)
        return touched

    def patch_many(self, changes: dict[str, dict[str, Any]]) -> int:
        """Change NAMED FIELDS of the CURRENT records, read and written under one lock.

            `changes` maps record id -> {field: value or fn(current_value) -> value}.
            Returns how many landed."""
        if not changes:
            return 0
        with state_file_lock(self.path):
            merged: list[MemoryRecord] = []
            landed = 0
            for rec in self._active_unlocked():
                fields = changes.get(rec.id)
                if fields:
                    update = {
                        name: (value(getattr(rec, name)) if callable(value) else value)
                        for name, value in fields.items()
                    }
                    rec = rec.model_copy(update=update)
                    landed += 1
                merged.append(rec)
            if landed:
                self._rewrite_unlocked(merged)
        return landed

    def update_many(self, records: Iterable[MemoryRecord]) -> int:
        """Replace several records in ONE rewrite. Returns how many landed."""
        by_id = {r.id: r for r in records}
        if not by_id:
            return 0
        with state_file_lock(self.path):
            current = self._active_unlocked()
            merged: list[MemoryRecord] = []
            landed = 0
            for existing in current:
                replacement = by_id.get(existing.id)
                if replacement is None:
                    merged.append(existing)
                else:
                    merged.append(replacement)
                    landed += 1
            if landed:
                self._rewrite_unlocked(merged)
        return landed

    def archive_record(self, record_id: str) -> bool:
        """Move a record from active store to archive. Returns True if moved."""
        # The active file stays locked across read, archive-append and rewrite: an append in that window would be dropped by the rewrite.
        with state_file_lock(self.path):
            records = self._active_unlocked()
            target = next((r for r in records if r.id == record_id), None)
            if target is None:
                return False
            target = target.model_copy(update={"archived": True})
            with state_file_lock(self.archive_path):   # different file, own lock
                append_state_jsonl_unlocked(
                    self.archive_path, [target.model_dump(mode="json")],
                )
            keep = [r for r in records if r.id != record_id]
            self._rewrite_unlocked(keep)
        return True

    # ---------- reads ----------

    def load(self) -> list[MemoryRecord]:
        """Full file scan — returns only LIVE (non-expired) records."""
        now = _dt.datetime.now(_dt.timezone.utc)
        # One lock over read and eviction rewrite, for the same reason as `update`.
        with state_file_lock(self.path):
            all_records = self._load_raw_unlocked()
            live = [rec for rec in all_records if not self._is_expired(rec, now)]
            # Lazy eviction: rewrite store only when expired records are found
            if len(live) != len(all_records):
                self._rewrite_unlocked(live)
        return live

    def _active_unlocked(self) -> list[MemoryRecord]:
        """Live (non-expired) records, read inside an already-held lock.

            TTL eviction is NOT performed here: the caller rewrites the surviving set itself, and a second rewrite would race with it."""
        now = _dt.datetime.now(_dt.timezone.utc)
        return [rec for rec in self._load_raw_unlocked() if not self._is_expired(rec, now)]

    @staticmethod
    def _is_expired(rec: MemoryRecord, now: _dt.datetime) -> bool:
        """Has this record's TTL run out as of ``now``?

            `active` and `_active_unlocked` must agree on WHICH records are alive, or a rewrite would keep rows the read path already stopped returning."""
        return record_is_expired(rec, now)

    def _rewrite_unlocked(self, records: list[MemoryRecord]) -> None:
        """Rewrite with `records`, keeping every stored row that does not validate exactly as it was (a row from a newer version must survive)."""
        unreadable = []
        for row in read_state_jsonl_unlocked(self.path) if self.path.exists() else []:
            try:
                MemoryRecord.model_validate(row)
            except ValueError:
                unreadable.append(row)
        rewrite_state_jsonl_unlocked(
            self.path, [r.model_dump(mode="json") for r in records] + unreadable,
        )

    def _load_raw_unlocked(self) -> list[MemoryRecord]:
        """Same as `_load_raw`, but the caller already holds the file lock."""
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        for row in read_state_jsonl_unlocked(self.path):
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def _load_raw(self) -> list[MemoryRecord]:
        """Load ALL records from disk without TTL filtering."""
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
        """Returns True if a record was actually removed.

            One lock over read and rewrite, for the same reason as `update`."""
        with state_file_lock(self.path):
            records = self._active_unlocked()
            keep = [r for r in records if r.id != record_id]
            if len(keep) == len(records):
                return False
            self._rewrite_unlocked(keep)
        return True

    def delete_all(self) -> int:
        with state_file_lock(self.path):
            n = len(self._active_unlocked())
            if self.path.exists():
                self.path.unlink()
        return n

    def drop_ids(self, ids: Iterable[str], *, add: Iterable[MemoryRecord] = ()) -> int:
        """Remove rows by id and append `add` in one locked rewrite; returns how many were removed."""
        gone = set(ids)
        extra = [record.model_dump(mode="json") for record in add]
        with state_file_lock(self.path):
            # Raw rows: one saved meanwhile or one that does not validate goes back as read.
            rows = read_state_jsonl_unlocked(self.path)
            keep = [row for row in rows if row.get("id") not in gone]
            if len(keep) != len(rows) or extra:
                rewrite_state_jsonl_unlocked(self.path, keep + extra)
        return len(rows) - len(keep)

    # ---------- helpers ----------

    def _rewrite(self, records: list[MemoryRecord]) -> None:
        with state_file_lock(self.path):
            self._rewrite_unlocked(records)


    def search_archive(self, term: str, *, limit: int = 10) -> list[MemoryRecord]:
        """Search the archive, read-only: a way to LOOK into archived memory, not to wake it.

            Returning a record to active memory is a separate authority, not this function."""
        needle = (term or "").strip().casefold()
        if not needle:
            return []
        hits = [
            record for record in self.load_archive()
            if needle in str(record.content).casefold()
        ]
        hits.sort(key=lambda r: str(r.created_at), reverse=True)
        return hits[:limit]
