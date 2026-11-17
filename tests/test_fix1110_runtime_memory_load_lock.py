"""`load()` evicts expired records; a save landing mid-eviction must survive."""
from __future__ import annotations

import contextlib
import datetime as dt
from pathlib import Path

from core import persistent_memory as pm
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


def _rec(rid: str, **extra) -> MemoryRecord:
    return MemoryRecord(id=rid, content=rid, tags=["fact"], source="test", **extra)


def test_a_save_between_read_and_ttl_rewrite_survives(tmp_path: Path, monkeypatch):
    path = tmp_path / "memory.jsonl"
    store = PersistentMemoryStore(path)
    store.save(_rec("mem_live"))
    long_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    store.save(_rec("mem_expired", ttl_seconds=1, created_at=long_ago))

    original_lock = pm.state_file_lock
    exits: list[int] = []

    @contextlib.contextmanager
    def _lock_that_lets_a_writer_in_on_exit(target):
        with original_lock(target):
            yield
        # The first exit is the gap between read and rewrite, if there is one.
        if str(target) == str(path) and not exits:
            exits.append(1)
            pm.append_state_jsonl_unlocked(path, [_rec("mem_new").model_dump(mode="json")])

    monkeypatch.setattr(pm, "state_file_lock", _lock_that_lets_a_writer_in_on_exit)
    live = store.load()
    monkeypatch.undo()

    assert exits, "the hook never fired"
    assert [r.id for r in live] == ["mem_live"]
    assert {r.id for r in PersistentMemoryStore(path).load()} == {"mem_live", "mem_new"}
