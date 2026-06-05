"""PersistentMemoryStore unit tests — save/load/delete on JSONL."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


def _record(content: str = "fact one", tags: list[str] | None = None) -> MemoryRecord:
    return MemoryRecord(
        type="semantic",
        content=content,
        tags=tags or ["fact"],
        owner="user",
    )


class TestStoreBasics:
    def test_load_empty_file_returns_empty_list(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        assert store.load() == []
        assert store.count() == 0

    def test_save_then_load_round_trip(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = _record("user prefers Python", ["preference"])
        store.save(rec)
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].id == rec.id
        assert loaded[0].content == "user prefers Python"
        assert loaded[0].tags == ["preference"]
        assert loaded[0].type == "semantic"

    def test_multiple_saves_preserve_order(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        r1 = _record("first")
        r2 = _record("second")
        r3 = _record("third")
        store.save(r1)
        store.save(r2)
        store.save(r3)
        loaded = store.load()
        assert [r.content for r in loaded] == ["first", "second", "third"]

    def test_save_many(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        n = store.save_many([_record("a"), _record("b")])
        assert n == 2
        assert store.count() == 2


class TestStorePersistsAcrossInstances:
    """Acceptance #2: a new session loads records written by an earlier one."""

    def test_new_store_instance_sees_previous_records(self, tmp_path: Path):
        path = tmp_path / "mem.jsonl"
        store_a = PersistentMemoryStore(path)
        store_a.save(_record("survives across sessions", ["fact"]))

        # Simulate a fresh session.
        store_b = PersistentMemoryStore(path)
        loaded = store_b.load()
        assert len(loaded) == 1
        assert loaded[0].content == "survives across sessions"


class TestStoreDeletion:
    def test_delete_by_id_removes_only_that_record(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        r1 = _record("keep")
        r2 = _record("drop")
        r3 = _record("keep too")
        store.save(r1)
        store.save(r2)
        store.save(r3)

        assert store.delete(r2.id) is True
        remaining = store.load()
        ids = {r.id for r in remaining}
        assert ids == {r1.id, r3.id}

    def test_delete_unknown_id_returns_false(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        store.save(_record())
        assert store.delete("mem_does_not_exist") is False
        assert store.count() == 1

    def test_delete_all_wipes_file(self, tmp_path: Path):
        path = tmp_path / "mem.jsonl"
        store = PersistentMemoryStore(path)
        store.save(_record("a"))
        store.save(_record("b"))
        assert store.delete_all() == 2
        assert not path.exists()
        assert store.load() == []


class TestStoreResilience:
    def test_corrupted_line_is_skipped(self, tmp_path: Path):
        path = tmp_path / "mem.jsonl"
        store = PersistentMemoryStore(path)
        good = _record("intact")
        store.save(good)

        # Append a non-JSON line and a JSON line that is not a MemoryRecord.
        with path.open("a", encoding="utf-8") as fh:
            fh.write("not-json at all\n")
            fh.write('{"random": "object"}\n')

        store2 = PersistentMemoryStore(path)
        loaded = store2.load()
        assert len(loaded) == 1
        assert loaded[0].id == good.id

    def test_blank_lines_are_skipped(self, tmp_path: Path):
        path = tmp_path / "mem.jsonl"
        store = PersistentMemoryStore(path)
        store.save(_record("a"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n\n\n")
        store.save(_record("b"))
        loaded = store.load()
        assert [r.content for r in loaded] == ["a", "b"]

    def test_get_returns_matching_record(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = _record("findable")
        store.save(rec)
        found = store.get(rec.id)
        assert found is not None
        assert found.content == "findable"

    def test_get_returns_none_for_missing(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        assert store.get("mem_missing") is None


class TestTTLEviction:
    """Records with ttl_seconds set must be evicted when expired."""

    def _expired_record(self, content: str = "stale") -> MemoryRecord:
        """Create a record that is already past its TTL."""
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)
        return MemoryRecord(
            type="semantic",
            content=content,
            tags=["ttl-test"],
            owner="user",
            ttl_seconds=60,         # 60 s TTL
            created_at=past,        # created 120 s ago → already expired
        )

    def _fresh_record(self, content: str = "fresh") -> MemoryRecord:
        """Create a record whose TTL has not yet elapsed."""
        now = dt.datetime.now(dt.timezone.utc)
        return MemoryRecord(
            type="semantic",
            content=content,
            tags=["ttl-test"],
            owner="user",
            ttl_seconds=3600,       # 1 h TTL
            created_at=now,         # just created → not expired
        )

    def _no_ttl_record(self, content: str = "immortal") -> MemoryRecord:
        """Create a record with no TTL (never expires)."""
        return MemoryRecord(
            type="semantic",
            content=content,
            tags=["no-ttl"],
            owner="user",
        )

    def test_expired_record_not_returned_by_load(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        store.save(self._expired_record("expired content"))
        assert store.load() == []

    def test_fresh_record_is_returned_by_load(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = self._fresh_record("fresh content")
        store.save(rec)
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].id == rec.id

    def test_no_ttl_record_always_returned(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = self._no_ttl_record("immortal")
        store.save(rec)
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].content == "immortal"

    def test_mixed_records_only_returns_live_ones(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        expired = self._expired_record("old")
        fresh = self._fresh_record("new")
        no_ttl = self._no_ttl_record("eternal")
        store.save(expired)
        store.save(fresh)
        store.save(no_ttl)
        loaded = store.load()
        contents = {r.content for r in loaded}
        assert contents == {"new", "eternal"}
        assert "old" not in contents

    def test_expired_records_evicted_from_disk(self, tmp_path: Path):
        """After load(), expired records must not be present in the file."""
        path = tmp_path / "mem.jsonl"
        store = PersistentMemoryStore(path)
        store.save(self._expired_record("stale"))
        store.save(self._no_ttl_record("keep"))
        # First load triggers eviction rewrite
        store.load()
        # Second load (fresh instance) must only see the live record
        store2 = PersistentMemoryStore(path)
        loaded = store2.load()
        assert len(loaded) == 1
        assert loaded[0].content == "keep"

    def test_count_excludes_expired(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        store.save(self._expired_record())
        store.save(self._fresh_record())
        assert store.count() == 1  # count() calls load() internally

    def test_naive_created_at_ttl_is_treated_as_utc(self, tmp_path: Path):
        """A record persisted with a tz-naive created_at must still be TTL-evicted.

        Covers the load() branch that backfills UTC tzinfo before comparing,
        so legacy/naive timestamps don't silently dodge expiry.
        """
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        naive_past = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=120)
        rec = MemoryRecord(
            type="semantic",
            content="naive stale",
            tags=["ttl-test"],
            owner="user",
            ttl_seconds=60,
            created_at=naive_past,
        )
        store.save(rec)
        assert store.load() == []


class TestStoreUpdate:
    def test_update_replaces_record_in_place(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = _record("before")
        store.save(rec)
        store.save(_record("other"))

        edited = rec.model_copy(update={"content": "after", "importance": 0.9})
        assert store.update(edited) is True

        loaded = {r.id: r for r in store.load()}
        assert loaded[rec.id].content == "after"
        assert loaded[rec.id].importance == 0.9
        # Sibling record is untouched and order preserved.
        assert [r.content for r in store.load()] == ["after", "other"]

    def test_update_unknown_id_returns_false_and_writes_nothing(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        store.save(_record("only"))
        ghost = _record("ghost")  # never saved → different id
        assert store.update(ghost) is False
        assert [r.content for r in store.load()] == ["only"]


class TestStoreArchive:
    def test_archive_record_moves_to_archive_store(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        keep = _record("keep")
        move = _record("low value")
        store.save(keep)
        store.save(move)

        assert store.archive_record(move.id) is True

        # Active store no longer holds it.
        active_ids = {r.id for r in store.load()}
        assert active_ids == {keep.id}
        # Archive holds it, flagged archived.
        archived = store.load_archive()
        assert len(archived) == 1
        assert archived[0].id == move.id
        assert archived[0].archived is True
        assert store.count_archive() == 1

    def test_archive_unknown_id_returns_false(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        store.save(_record("present"))
        assert store.archive_record("mem_missing") is False
        assert store.count_archive() == 0

    def test_load_archive_empty_when_no_archive_file(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        assert store.load_archive() == []
        assert store.count_archive() == 0

    def test_load_archive_skips_corrupted_lines(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        rec = _record("real archived")
        store.save(rec)
        store.archive_record(rec.id)

        # Corrupt the archive file with junk lines.
        with store.archive_path.open("a", encoding="utf-8") as fh:
            fh.write("not-json\n")
            fh.write('{"not": "a record"}\n')

        loaded = store.load_archive()
        assert len(loaded) == 1
        assert loaded[0].id == rec.id


