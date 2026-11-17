"""Memory hygiene and bulk delete must not lose a record saved while they run."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core.memory_hygiene as hygiene
from core import persistent_memory
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.state_integrity import append_state_jsonl, read_state_jsonl

OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _rec(text: str, **kwargs) -> MemoryRecord:
    return MemoryRecord(content=text, tags=kwargs.pop("tags", ["fact"]), owner="user", **kwargs)


def _late(store: PersistentMemoryStore) -> MemoryRecord:
    """A record another process saves in the middle of a hygiene pass."""
    late = _rec("saved by another process meanwhile")
    store.save(late)
    return late


def _once(fn):
    """Run the wrapped side effect on the first call only."""
    state = {"done": False}

    def run() -> None:
        if not state["done"]:
            state["done"] = True
            fn()
    return run


def test_dedup_keeps_a_record_saved_during_the_pass(tmp_path: Path, monkeypatch) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    first = _rec("the sky is blue today", created_at=OLD)
    copy = _rec("the sky is blue today", created_at=OLD + timedelta(days=1))
    store.save_many([first, copy])
    late: list[MemoryRecord] = []
    save_late = _once(lambda: late.append(_late(store)))
    real = hygiene.find_duplicate

    def find_and_interleave(*args, **kwargs):
        save_late()
        return real(*args, **kwargs)

    monkeypatch.setattr(hygiene, "find_duplicate", find_and_interleave)

    report = hygiene.deduplicate_memory(store)

    assert report.deleted == [copy.id]
    assert sorted(r.id for r in store.load()) == sorted([first.id, late[0].id])


def test_expiry_keeps_a_record_saved_during_the_pass(tmp_path: Path, monkeypatch) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    stale = _rec("short lived", ttl_seconds=60, created_at=OLD)
    kept = _rec("forever")
    append_state_jsonl(store.path, [stale.model_dump(mode="json"), kept.model_dump(mode="json")])
    late: list[MemoryRecord] = []
    save_late = _once(lambda: late.append(_late(store)))
    real = hygiene._is_expired

    def expired_and_interleave(record, now):
        save_late()
        return real(record, now)

    monkeypatch.setattr(hygiene, "_is_expired", expired_and_interleave)

    report = hygiene.expire_memory(store)

    assert report.expired == [stale.id]
    assert sorted(r.id for r in store.load()) == sorted([kept.id, late[0].id])


class _SlowLLM:
    def __init__(self, store: PersistentMemoryStore) -> None:
        self.store = store
        self.late: MemoryRecord | None = None

    def complete(self, system: str, user: str, max_tokens: int = 0, temperature: float = 0.0) -> str:
        self.late = _late(self.store)
        return "merged"


def test_summarise_keeps_a_record_saved_while_the_model_answers(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    a, b = _rec("project fact one", tags=["project"]), _rec("another project fact", tags=["project"])
    store.save_many([a, b])
    llm = _SlowLLM(store)

    report = hygiene.summarise_memory(store, llm, tag="project")

    ids = {r.id for r in store.load()}
    assert ids == {report.new_record_id, llm.late.id}


def test_hygiene_rewrite_keeps_a_row_the_model_cannot_validate(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    stale = _rec("short lived", ttl_seconds=60, created_at=OLD)
    odd = {"id": "mem_odd", "type": "unknown-type", "content": "ручная запись"}
    append_state_jsonl(store.path, [stale.model_dump(mode="json"), odd])

    hygiene.expire_memory(store)

    assert read_state_jsonl(store.path) == [odd]


def test_drop_ids_reports_how_many_rows_left(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    a, b = _rec("one"), _rec("two")
    store.save_many([a, b])

    assert store.drop_ids([a.id, "mem_missing"]) == 1
    assert [r.id for r in store.load()] == [b.id]


def test_delete_all_counts_exactly_what_it_deletes(tmp_path: Path, monkeypatch) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    store.save_many([_rec("one"), _rec("two")])
    real_lock = persistent_memory.state_file_lock
    save_late = _once(lambda: append_state_jsonl(store.path, [_rec("three").model_dump(mode="json")]))

    @contextmanager
    def lock_then_interleave(path):
        with real_lock(path):
            yield
        save_late()

    monkeypatch.setattr(persistent_memory, "state_file_lock", lock_then_interleave)

    deleted = store.delete_all()

    monkeypatch.undo()
    assert deleted == 3 - len(store.load())
