"""PersistentMemoryStore unit tests — save/load/delete on JSONL."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


def _record(content: str = "fact one", tags: list[str] | None = None) -> MemoryRecord:
    return MemoryRecord(
        type="semantic",
        content=content,
        tags=tags or ["fact"],
        owner="user",
    )


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


    def test_get_returns_none_for_missing(self, tmp_path: Path):
        store = PersistentMemoryStore(tmp_path / "mem.jsonl")
        assert store.get("mem_missing") is None


class TestTTLEviction:
    """Records with ttl_seconds set must be evicted when expired."""


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
