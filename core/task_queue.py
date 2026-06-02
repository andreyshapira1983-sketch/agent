"""Persistent task queue for autonomous runtime work.

The first autonomous runtime could run a bounded dry-run pass, but it had no
memory between launches. This queue is the durable handoff: scheduler ticks,
CLI commands, and future monitors can enqueue work; the runtime can claim a
pending task, run it, and record the result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.file_lock import exclusive_file_lock
from core.ids import new_id
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked


RuntimeTaskKind = Literal["auto_run"]
RuntimeTaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
_VALID_KINDS = {"auto_run"}
_VALID_STATUSES = {"pending", "running", "done", "failed", "cancelled"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _choice(value: object, *, default: str, allowed: set[str], field_name: str) -> str:
    out = str(value or default)
    if out not in allowed:
        raise ValueError(f"invalid {field_name}: {out}")
    return out


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _iso_field(value: object, *, default: str) -> str:
    out = str(value or default)
    _parse_iso(out)
    return out


@dataclass(frozen=True)
class RuntimeTask:
    kind: RuntimeTaskKind
    goal: str
    id: str = field(default_factory=lambda: new_id("rtask"))
    status: RuntimeTaskStatus = "pending"
    priority: int = 5
    run_after: str = field(default_factory=_iso)
    attempts: int = 0
    max_attempts: int = 1
    dry_run: bool = True
    include_tests: bool = True
    limit: int = 5
    learning_limit: int = 5
    last_error: str = ""
    last_report: dict | None = None
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeTask":
        return cls(
            id=str(data.get("id") or new_id("rtask")),
            kind=_choice(data.get("kind"), default="auto_run", allowed=_VALID_KINDS, field_name="kind"),  # type: ignore[arg-type]
            goal=str(data.get("goal") or "project health"),
            status=_choice(data.get("status"), default="pending", allowed=_VALID_STATUSES, field_name="status"),  # type: ignore[arg-type]
            priority=int(data.get("priority", 5)),
            run_after=_iso_field(data.get("run_after"), default=_iso()),
            attempts=int(data.get("attempts", 0)),
            max_attempts=max(1, int(data.get("max_attempts", 1))),
            dry_run=_bool(data.get("dry_run"), default=True),
            include_tests=_bool(data.get("include_tests"), default=True),
            limit=max(1, int(data.get("limit", 5))),
            learning_limit=max(1, int(data.get("learning_limit", 5))),
            last_error=str(data.get("last_error") or ""),
            last_report=data.get("last_report") if isinstance(data.get("last_report"), dict) else None,
            created_at=str(data.get("created_at") or _iso()),
            updated_at=str(data.get("updated_at") or _iso()),
        )

    def with_updates(self, **updates) -> "RuntimeTask":
        data = self.to_dict()
        data.update(updates)
        data["updated_at"] = _iso()
        return RuntimeTask.from_dict(data)


class TaskQueueStore:
    """JSONL-backed runtime task queue.

    The file is rewritten on status updates. That keeps the current state easy
    to inspect by hand and avoids event-log compaction for this early slice.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        *,
        goal: str,
        kind: RuntimeTaskKind = "auto_run",
        run_after: datetime | None = None,
        priority: int = 5,
        max_attempts: int = 1,
        dry_run: bool = True,
        include_tests: bool = True,
        limit: int = 5,
        learning_limit: int = 5,
    ) -> RuntimeTask:
        task = RuntimeTask(
            kind=kind,
            goal=goal.strip() or "project health",
            priority=priority,
            run_after=_iso(run_after),
            max_attempts=max_attempts,
            dry_run=dry_run,
            include_tests=include_tests,
            limit=limit,
            learning_limit=learning_limit,
        )
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            tasks.append(task)
            self._save_unlocked(tasks)
        return task

    def load(self) -> list[RuntimeTask]:
        with exclusive_file_lock(self._lock_path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[RuntimeTask]:
        if not self.path.exists():
            return []
        tasks: list[RuntimeTask] = []
        for raw in read_state_jsonl_unlocked(self.path):
            try:
                tasks.append(RuntimeTask.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return tasks

    def list(self, *, status: RuntimeTaskStatus | str | None = None) -> list[RuntimeTask]:
        tasks = self.load()
        if status in (None, "", "all"):
            return tasks
        return [task for task in tasks if task.status == status]

    def pending(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[RuntimeTask]:
        now = (now or _now()).astimezone(timezone.utc)
        out = [
            task for task in self.load()
            if task.status == "pending" and _parse_iso(task.run_after) <= now
        ]
        out.sort(key=lambda task: (task.priority, _parse_iso(task.run_after), task.created_at))
        return out[:limit] if limit is not None else out

    def pending_by_ids(
        self,
        task_ids: list[str] | tuple[str, ...],
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[RuntimeTask]:
        now = (now or _now()).astimezone(timezone.utc)
        by_id = {task.id: task for task in self.load()}
        out: list[RuntimeTask] = []
        for task_id in task_ids:
            task = by_id.get(task_id)
            if task is None or task.status != "pending":
                continue
            if _parse_iso(task.run_after) <= now:
                out.append(task)
        return out[:limit] if limit is not None else out

    def get(self, task_id: str) -> RuntimeTask | None:
        for task in self.load():
            if task.id == task_id:
                return task
        return None

    def mark_running(self, task_id: str) -> RuntimeTask:
        task = self._update_one(
            task_id,
            lambda task: task.with_updates(
                status="running",
                attempts=task.attempts + 1,
                last_error="",
            ),
        )
        return task

    def mark_done(self, task_id: str, *, report: dict | None = None) -> RuntimeTask:
        return self._update_one(
            task_id,
            lambda task: task.with_updates(
                status="done",
                last_report=report or task.last_report,
                last_error="",
            ),
        )

    def mark_failed(self, task_id: str, *, error: str, report: dict | None = None) -> RuntimeTask:
        def update(task: RuntimeTask) -> RuntimeTask:
            status: RuntimeTaskStatus = "failed" if task.attempts >= task.max_attempts else "pending"
            return task.with_updates(
                status=status,
                last_error=error,
                last_report=report or task.last_report,
            )
        return self._update_one(task_id, update)

    def cancel(self, task_id: str) -> RuntimeTask:
        return self._update_one(task_id, lambda task: task.with_updates(status="cancelled"))

    def recover_stuck(self, *, timeout_minutes: int = 30) -> list[RuntimeTask]:
        """Reset tasks stuck in ``'running'`` state back to ``'pending'``.

        A task is considered stuck when its ``updated_at`` timestamp is older
        than *timeout_minutes* minutes.  This can happen if the agent process
        was killed mid-run and never transitioned the task to a terminal state.

        Returns the list of tasks that were recovered (empty if none).
        """
        cutoff_ts = datetime.now(tz=timezone.utc).timestamp() - timeout_minutes * 60
        recovered: list[RuntimeTask] = []
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            out: list[RuntimeTask] = []
            for task in tasks:
                if task.status == "running":
                    try:
                        updated_ts = datetime.fromisoformat(
                            task.updated_at.replace("Z", "+00:00")
                        ).timestamp()
                    except (ValueError, AttributeError):
                        updated_ts = 0.0  # unparseable → treat as very old
                    if updated_ts < cutoff_ts:
                        fixed = task.with_updates(
                            status="pending",
                            last_error="recovered from stuck running state",
                        )
                        out.append(fixed)
                        recovered.append(fixed)
                    else:
                        out.append(task)
                else:
                    out.append(task)
            if recovered:
                self._save_unlocked(out)
        return recovered

    def summary(self) -> dict:
        tasks = self.load()
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return {
            "path": str(self.path),
            "total": len(tasks),
            "statuses": counts,
            "pending_due": len(self.pending()),
        }

    def _update_one(self, task_id: str, fn) -> RuntimeTask:
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            updated: RuntimeTask | None = None
            out: list[RuntimeTask] = []
            for task in tasks:
                if task.id == task_id:
                    updated = fn(task)
                    out.append(updated)
                else:
                    out.append(task)
            if updated is None:
                raise KeyError(f"task not found: {task_id}")
            self._save_unlocked(out)
            return updated

    @property
    def _lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    def _save_unlocked(self, tasks: list[RuntimeTask]) -> None:
        rewrite_state_jsonl_unlocked(self.path, [task.to_dict() for task in tasks])
