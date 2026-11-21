"""Retire parked (budget-paused) checkpoints older than the resumable-age ceiling.

Lists the stale tasks with their goals and marks them failed, so the retirement is a
visible, backed-up act instead of a side effect of the next tick. Dry run by default;
--apply writes after backing the store up.

Usage: python scripts/retire_stale_checkpoints.py [--workspace PATH] [--apply]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.file_lock import exclusive_file_lock
from core.state_integrity import backup_state_file
from core.task_queue import (
    _MAX_RESUMABLE_AGE_HOURS,
    RuntimeTask,
    TaskQueueStore,
    _is_resource_paused,
)

REASON = f"stale checkpoint older than {_MAX_RESUMABLE_AGE_HOURS}h"


def _age_hours(task: RuntimeTask, now: datetime) -> float:
    try:
        parked = datetime.fromisoformat(task.updated_at).astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return float("inf")  # unparseable → treat as long past
    return (now - parked).total_seconds() / 3600


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    path = Path(args.workspace) / "data" / "runtime_tasks.jsonl"
    store = TaskQueueStore(path=path)
    tasks = store.list()

    stale = [
        t for t in tasks
        if _is_resource_paused(t) and _age_hours(t, now) > _MAX_RESUMABLE_AGE_HOURS
    ]
    fresh = [
        t for t in tasks
        if _is_resource_paused(t) and _age_hours(t, now) <= _MAX_RESUMABLE_AGE_HOURS
    ]

    print(f"store: {path}  ({len(tasks)} rows)")
    print(f"ceiling: {_MAX_RESUMABLE_AGE_HOURS}h")
    print(f"to retire: {len(stale)}   left resumable: {len(fresh)}")
    for t in sorted(stale, key=lambda x: x.updated_at):
        goal = " ".join(str(t.goal or "").split())[:88]
        print(f"  {t.updated_at[:19]}  {t.id[:18]}  {goal}")

    if not args.apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        return 0
    if not stale:
        print("\nnothing to do.")
        return 0

    backup = backup_state_file(path)
    print(f"\nbackup: {backup}")
    stale_ids = {t.id for t in stale}
    # Under the store's own lock: a running daemon tick must not race this rewrite.
    with exclusive_file_lock(store._lock_path):
        store._save_unlocked([
            t.with_updates(status="failed",
                           last_error=f"{t.last_error or 'paused'}; {REASON}")
            if t.id in stale_ids else t
            for t in store._load_unlocked()
        ])
    print(f"retired {len(stale)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
