"""A task must not be writable in a shape the reader will silently drop.

Found 2026-08-04 while probing task recovery: `add(kind="status")` — a kind
that does not exist — was accepted, serialised and written to disk (626 bytes
on the file), and the very next `load()` returned an empty list. `from_dict`
rejects the row, `_load_unlocked` swallows the `ValueError` and moves on, so
the task is gone with no exception and no log line.

Validation lives only on the read side (`_choice`, `core/task_queue.py:132`);
the write side (`add`, `:211`) constructs `RuntimeTask` directly. Whoever
queued the work would see it accepted and never executed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.state_integrity import append_state_jsonl_unlocked
from core.task_queue import TaskQueueStore


def test_an_unknown_kind_is_refused_at_write_time(tmp_path: Path):
    """The error belongs where the mistake is made, not three reads later."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")

    with pytest.raises(ValueError, match="kind"):
        store.add(goal="probe", kind="status")       # type: ignore[arg-type]


def test_a_refused_task_leaves_nothing_behind(tmp_path: Path):
    """A rejected write must not leave an unreadable row on disk."""
    path = tmp_path / "tasks.jsonl"
    store = TaskQueueStore(path)

    with pytest.raises(ValueError):
        store.add(goal="probe", kind="status")       # type: ignore[arg-type]

    assert store.load() == []
    assert not path.exists() or path.read_text(encoding="utf-8").strip() == ""


def test_unreadable_rows_are_counted_not_just_skipped(tmp_path: Path):
    """A row the reader cannot parse must leave a trace, not vanish.

    The row is written through the real encoder: intact envelope, valid
    checksum, only the *schema* is wrong. That is exactly the row the old
    `add(kind="status")` produced — a plain-JSON line would instead be taken
    for a legacy payload and quietly filled with defaults.
    """
    path = tmp_path / "tasks.jsonl"
    store = TaskQueueStore(path)
    store.add(goal="good one", kind="auto_run")
    append_state_jsonl_unlocked(path, [{"id": "rtask_bad", "kind": "nonesuch"}])

    tasks = store.load()

    assert len(tasks) == 1, "the good task must still load"
    assert store.last_unreadable_rows == 1, (
        "an unparseable row was skipped without leaving any trace"
    )


def test_the_counter_describes_the_last_read_not_an_older_one(tmp_path: Path):
    """A rotated-away queue must not keep reporting the rows of a read before it.

    `_load_unlocked` returns early when the file is gone. That path used to
    leave `last_unreadable_rows` at its previous value, so a caller reading it
    after an empty load was told rows had been dropped from a file that no
    longer exists — a stale number, in the one field whose whole purpose is to
    say what THIS read lost.
    """
    path = tmp_path / "tasks.jsonl"
    store = TaskQueueStore(path)
    store.add(goal="good one", kind="auto_run")
    append_state_jsonl_unlocked(path, [{"id": "rtask_bad", "kind": "nonesuch"}])
    store.load()
    assert store.last_unreadable_rows == 1, "нужен ненулевой счётчик до отката"

    path.unlink()

    assert store.load() == []
    assert store.last_unreadable_rows == 0, (
        "счётчик пережил чтение, в котором нечего было терять"
    )


def test_a_valid_kind_still_works(tmp_path: Path):
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="probe", kind="auto_run")

    assert [t.id for t in store.load()] == [task.id]
    assert store.last_unreadable_rows == 0
