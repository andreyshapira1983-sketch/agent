"""A concurrent save must not vanish inside update/delete/archive.

`PersistentMemoryStore.update` reads the whole file, edits the list in memory
and rewrites it. Between the read and the rewrite the file is unguarded, so an
append made by another writer in that window is overwritten without a trace —
no error, no log line, the record is simply gone.

The neighbouring methods already take `state_file_lock` (`core/persistent_memory.py:47,54,81`);
the read-modify-write pair does not. These tests pin that the window is closed.
"""
from __future__ import annotations

from pathlib import Path

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


def _rec(rid: str, content: str = "x") -> MemoryRecord:
    return MemoryRecord(id=rid, content=content, tags=["fact"], source="test")


def test_a_save_in_the_gap_between_read_and_rewrite_survives(tmp_path: Path, monkeypatch):
    """Model the window exactly: write when the first critical section EXITS.

    Old code: `load()` took the lock, released it, and only then `_rewrite`
    took it again — a save landing in that gap was overwritten. New code holds
    one lock across both, so the first exit happens after the rewrite and the
    save cannot be lost.

    Two earlier attempts at this test passed on the broken code, because both
    hooks sat INSIDE a critical section, where even the old code was safe.
    """
    path = tmp_path / "memory.jsonl"
    store = PersistentMemoryStore(path)
    store.save(_rec("mem_a", "first"))

    import contextlib

    from core import persistent_memory as pm

    original_lock = pm.state_file_lock
    exits: list[int] = []

    @contextlib.contextmanager
    def _lock_that_lets_a_writer_in_on_exit(target):
        with original_lock(target):
            yield
        # Exiting: in the old code this is the unguarded gap.
        if str(target) == str(path) and not exits:
            exits.append(1)
            pm.append_state_jsonl_unlocked(
                path, [_rec("mem_b", "landed in the gap").model_dump(mode="json")]
            )

    monkeypatch.setattr(pm, "state_file_lock", _lock_that_lets_a_writer_in_on_exit)
    store.update(_rec("mem_a", "edited"))
    monkeypatch.undo()

    assert exits, "the hook never fired — the model does not exercise the code"
    records = {r.id: r.content for r in PersistentMemoryStore(path).load()}
    assert records.get("mem_a") == "edited", "the edited record is missing"
    assert "mem_b" in records, (
        "the save that landed between read and rewrite was overwritten — "
        "the two are not under one lock"
    )


def test_update_still_edits_in_place(tmp_path: Path):
    """The guard must not change what update does when nobody competes."""
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    store.save(_rec("mem_a", "first"))

    assert store.update(_rec("mem_a", "edited")) is True

    records = {r.id: r.content for r in store.load()}
    assert records == {"mem_a": "edited"}


def test_update_reports_a_miss(tmp_path: Path):
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    store.save(_rec("mem_a"))

    assert store.update(_rec("mem_absent")) is False
