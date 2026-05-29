"""PersistentMemoryStore unit tests — save/load/delete on JSONL."""
from __future__ import annotations

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
