"""Task rows the reader cannot parse survive rewrites; the scheduler shows its unparsed rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.scheduler import SchedulerStore
from core.state_integrity import append_state_jsonl, read_state_jsonl, rewrite_state_jsonl
from core.task_queue import TaskQueueStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BAD_TASK = {"id": "rtask_bad", "kind": "nonesuch", "goal": "чужая работа"}


def _queue_with_bad_row(tmp_path: Path) -> tuple[TaskQueueStore, str]:
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    good = store.add(goal="good one")
    append_state_jsonl(store.path, [BAD_TASK])
    return store, good.id


def _ids(store: TaskQueueStore) -> list[str]:
    return sorted(row["id"] for row in read_state_jsonl(store.path))


# ── task queue ───────────────────────────────────────────────────────────────

def test_add_keeps_a_task_row_it_cannot_parse(tmp_path: Path) -> None:
    store, good_id = _queue_with_bad_row(tmp_path)

    added = store.add(goal="another")

    assert _ids(store) == sorted([good_id, added.id, "rtask_bad"])
    assert BAD_TASK in read_state_jsonl(store.path)


# ── scheduler ────────────────────────────────────────────────────────────────

def _store_with_hand_edit(tmp_path: Path) -> SchedulerStore:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    far = NOW + timedelta(days=1)
    store.add(name="good", goal="g1", every_minutes=60, start_at=far)
    store.add(name="hand-edited", goal="g2", every_minutes=60, start_at=far)
    rows = read_state_jsonl(store.path)
    rows[1]["status"] = "Active"
    rewrite_state_jsonl(store.path, rows)
    return store


def test_summary_shows_rows_the_scheduler_cannot_parse(tmp_path: Path) -> None:
    summary = _store_with_hand_edit(tmp_path).summary()

    assert summary["total"] == 1
    assert summary["unparsed"] == 1


def test_clean_summary_reports_zero_unparsed(tmp_path: Path) -> None:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    store.add(name="due", goal="g1", every_minutes=60, start_at=NOW)

    assert store.summary() == {"path": str(store.path), "total": 1, "statuses": {"active": 1},
                               "due": 1, "unparsed": 0}


def test_tick_rejects_a_zero_limit(tmp_path: Path) -> None:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    store.add(name="due", goal="g1", every_minutes=60, start_at=NOW)
    queue = TaskQueueStore(tmp_path / "tasks.jsonl")

    with pytest.raises(ValueError, match="limit must be >= 1"):
        store.tick(task_queue=queue, now=NOW, limit=0)

    assert queue.load() == []
    assert len(store.due(now=NOW)) == 1
