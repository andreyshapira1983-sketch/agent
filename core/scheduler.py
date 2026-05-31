"""Persistent scheduler for autonomous runtime tasks.

This is deliberately not a background daemon yet. A scheduler "tick" checks
which schedules are due, enqueues concrete runtime tasks, advances their next
run time, and exits. A future service can call the same tick periodically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from core.file_lock import exclusive_file_lock
from core.ids import new_id
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked
from core.task_queue import RuntimeTask, TaskQueueStore


ScheduleStatus = Literal["active", "paused"]
_VALID_STATUSES = {"active", "paused"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _status(value: object, *, default: str = "active") -> str:
    out = str(value or default)
    if out not in _VALID_STATUSES:
        raise ValueError(f"invalid schedule status: {out}")
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
class RuntimeSchedule:
    name: str
    goal: str
    every_minutes: int
    id: str = field(default_factory=lambda: new_id("sched"))
    status: ScheduleStatus = "active"
    next_run_at: str = field(default_factory=_iso)
    last_run_at: str | None = None
    dry_run: bool = True
    include_tests: bool = True
    limit: int = 5
    learning_limit: int = 5
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeSchedule":
        return cls(
            id=str(data.get("id") or new_id("sched")),
            name=str(data.get("name") or "schedule"),
            goal=str(data.get("goal") or "project health"),
            every_minutes=max(1, int(data.get("every_minutes", 60))),
            status=_status(data.get("status")),  # type: ignore[arg-type]
            next_run_at=_iso_field(data.get("next_run_at"), default=_iso()),
            last_run_at=_iso_field(data.get("last_run_at"), default=_iso()) if data.get("last_run_at") else None,
            dry_run=_bool(data.get("dry_run"), default=True),
            include_tests=_bool(data.get("include_tests"), default=True),
            limit=max(1, int(data.get("limit", 5))),
            learning_limit=max(1, int(data.get("learning_limit", 5))),
            created_at=str(data.get("created_at") or _iso()),
            updated_at=str(data.get("updated_at") or _iso()),
        )

    def with_updates(self, **updates) -> "RuntimeSchedule":
        data = self.to_dict()
        data.update(updates)
        data["updated_at"] = _iso()
        return RuntimeSchedule.from_dict(data)


@dataclass(frozen=True)
class ScheduleTickReport:
    due_count: int
    enqueued_count: int
    task_ids: tuple[str, ...]
    schedule_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "due_count": self.due_count,
            "enqueued_count": self.enqueued_count,
            "task_ids": list(self.task_ids),
            "schedule_ids": list(self.schedule_ids),
        }

    def user_summary(self) -> str:
        return (
            f"(scheduler tick: due={self.due_count}; "
            f"enqueued={self.enqueued_count}; tasks={list(self.task_ids)})"
        )


class SchedulerStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        *,
        name: str,
        goal: str,
        every_minutes: int,
        start_at: datetime | None = None,
        dry_run: bool = True,
        include_tests: bool = True,
        limit: int = 5,
        learning_limit: int = 5,
    ) -> RuntimeSchedule:
        if every_minutes < 1:
            raise ValueError("every_minutes must be >= 1")
        schedule = RuntimeSchedule(
            name=name.strip() or "schedule",
            goal=goal.strip() or "project health",
            every_minutes=every_minutes,
            next_run_at=_iso(start_at),
            dry_run=dry_run,
            include_tests=include_tests,
            limit=limit,
            learning_limit=learning_limit,
        )
        with exclusive_file_lock(self._lock_path):
            schedules = self._load_unlocked()
            schedules.append(schedule)
            self._save_unlocked(schedules)
        return schedule

    def load(self) -> list[RuntimeSchedule]:
        with exclusive_file_lock(self._lock_path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[RuntimeSchedule]:
        if not self.path.exists():
            return []
        schedules: list[RuntimeSchedule] = []
        for raw in read_state_jsonl_unlocked(self.path):
            try:
                schedules.append(RuntimeSchedule.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return schedules

    def list(self, *, status: ScheduleStatus | str | None = None) -> list[RuntimeSchedule]:
        schedules = self.load()
        if status in (None, "", "all"):
            return schedules
        return [schedule for schedule in schedules if schedule.status == status]

    def due(self, *, now: datetime | None = None, limit: int | None = None) -> list[RuntimeSchedule]:
        now = (now or _now()).astimezone(timezone.utc)
        out = [
            schedule for schedule in self.load()
            if schedule.status == "active" and _parse_iso(schedule.next_run_at) <= now
        ]
        out.sort(key=lambda schedule: (_parse_iso(schedule.next_run_at), schedule.created_at))
        return out[:limit] if limit is not None else out

    def tick(
        self,
        *,
        task_queue: TaskQueueStore,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> ScheduleTickReport:
        now = (now or _now()).astimezone(timezone.utc)
        with exclusive_file_lock(self._lock_path):
            schedules = self._load_unlocked()
            due_schedules = self._due_from(schedules, now=now, limit=limit)
            task_ids: list[str] = []
            updated_by_id: dict[str, RuntimeSchedule] = {}

            for schedule in due_schedules:
                task = task_queue.add(
                    goal=schedule.goal,
                    run_after=now,
                    dry_run=schedule.dry_run,
                    include_tests=schedule.include_tests,
                    limit=schedule.limit,
                    learning_limit=schedule.learning_limit,
                )
                task_ids.append(task.id)
                next_run = now + timedelta(minutes=schedule.every_minutes)
                updated_by_id[schedule.id] = schedule.with_updates(
                    last_run_at=_iso(now),
                    next_run_at=_iso(next_run),
                )

            updated = [updated_by_id.get(schedule.id, schedule) for schedule in schedules]
            self._save_unlocked(updated)

        return ScheduleTickReport(
            due_count=len(due_schedules),
            enqueued_count=len(task_ids),
            task_ids=tuple(task_ids),
            schedule_ids=tuple(schedule.id for schedule in due_schedules),
        )

    def pause(self, schedule_id: str) -> RuntimeSchedule:
        return self._update_one(schedule_id, lambda s: s.with_updates(status="paused"))

    def resume(self, schedule_id: str) -> RuntimeSchedule:
        return self._update_one(schedule_id, lambda s: s.with_updates(status="active"))

    def summary(self) -> dict:
        schedules = self.load()
        counts: dict[str, int] = {}
        for schedule in schedules:
            counts[schedule.status] = counts.get(schedule.status, 0) + 1
        return {
            "path": str(self.path),
            "total": len(schedules),
            "statuses": counts,
            "due": len(self.due()),
        }

    def _update_one(self, schedule_id: str, fn) -> RuntimeSchedule:
        with exclusive_file_lock(self._lock_path):
            schedules = self._load_unlocked()
            updated: RuntimeSchedule | None = None
            out: list[RuntimeSchedule] = []
            for schedule in schedules:
                if schedule.id == schedule_id:
                    updated = fn(schedule)
                    out.append(updated)
                else:
                    out.append(schedule)
            if updated is None:
                raise KeyError(f"schedule not found: {schedule_id}")
            self._save_unlocked(out)
            return updated

    @property
    def _lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    def _due_from(
        self,
        schedules: list[RuntimeSchedule],
        *,
        now: datetime,
        limit: int | None = None,
    ) -> list[RuntimeSchedule]:
        out = [
            schedule for schedule in schedules
            if schedule.status == "active" and _parse_iso(schedule.next_run_at) <= now
        ]
        out.sort(key=lambda schedule: (_parse_iso(schedule.next_run_at), schedule.created_at))
        return out[:limit] if limit is not None else out

    def _save_unlocked(self, schedules: list[RuntimeSchedule]) -> None:
        rewrite_state_jsonl_unlocked(self.path, [schedule.to_dict() for schedule in schedules])
