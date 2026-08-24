"""Persistent scheduler for autonomous runtime tasks.

This is deliberately not a background daemon yet. A scheduler "tick" checks
which schedules are due, enqueues concrete runtime tasks, advances their next
run time, and exits. A future service can call the same tick periodically.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from core.file_lock import exclusive_file_lock
from core.ids import new_id
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked
from core.task_queue import TaskQueueStore

logger = logging.getLogger(__name__)


ScheduleStatus = Literal["active", "paused", "disabled"]
_VALID_STATUSES = {"active", "paused", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    """Отметку без зоны считать UTC, а не местным временем.

    H-25 в docs/audit/HISTORICAL_FAILURE_LEDGER.md — класс «две конвенции, ни
    одной сквозной проверки» (Mars Climate Orbiter, только во времени).
    `datetime.fromisoformat("2026-08-24T10:00:00").astimezone(utc)` трактует
    наивное значение как МЕСТНОЕ и на этой машине даёт 07:00Z — тихий сдвиг на
    три часа. При сроке сиротства в 30 минут это значит, что живая задача
    мгновенно «осиротела», то есть открывается класс двойного исполнения,
    который MIR-033 измерил закрытым.

    В живом состоянии таких отметок нет: замер 2026-08-24 — 12 472 отметки, все
    с зоной. Механизм при этом жив, а все писатели репозитория пишут UTC,
    поэтому наивное значение приводится к UTC вместо местного: тихая ошибка
    становится нулевой, и ни одно чтение не падает на правке файла руками.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    def from_dict(cls, data: dict) -> RuntimeSchedule:
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

    def with_updates(self, **updates) -> RuntimeSchedule:
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
    #: How many schedules the store held when this tick ran — every record, not
    #: only the due ones. Deliberately has NO default: the store knows the number
    #: and a producer that cannot state it must say so at the call site rather
    #: than silently emit 0.
    #:
    #: Added 2026-08-14 (C16). Without it one journal line answered three
    #: different questions identically — no schedules configured at all, which is
    #: the live workspace today; schedules present with none due; and a severed
    #: wire. The operator's question is "why is my agent doing nothing?", and
    #: `due_count=0` alone cannot tell an unconfigured daemon from a patient one.
    total_count: int

    def to_dict(self) -> dict:
        return {
            "total_count": self.total_count,
            "due_count": self.due_count,
            "enqueued_count": self.enqueued_count,
            "task_ids": list(self.task_ids),
            "schedule_ids": list(self.schedule_ids),
        }

    def user_summary(self) -> str:
        if self.total_count == 0:
            return "(scheduler tick: no schedules configured — nothing to run)"
        return (
            f"(scheduler tick: {self.total_count} schedule(s); "
            f"due={self.due_count}; "
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
            total_count=len(schedules),
            due_count=len(due_schedules),
            enqueued_count=len(task_ids),
            task_ids=tuple(task_ids),
            schedule_ids=tuple(schedule.id for schedule in due_schedules),
        )

    def pause(self, schedule_id: str) -> RuntimeSchedule:
        return self._update_one(schedule_id, lambda s: s.with_updates(status="paused"))

    def resume(self, schedule_id: str) -> RuntimeSchedule:
        return self._update_one(schedule_id, lambda s: s.with_updates(status="active"))

    def disable(self, schedule_id: str) -> RuntimeSchedule:
        return self._update_one(schedule_id, lambda s: s.with_updates(status="disabled"))

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


# ── in-loop timer scheduler (plan item 2.1) ──────────────────────────────────

NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
TickCallback = Callable[[ScheduleTickReport], "Awaitable[None] | None"]

# Fallback wait (seconds) used when no active schedule has a next-run time yet.
# The service still re-evaluates after this bound, so a schedule added by an
# out-of-band writer is picked up without an unbounded sleep — but it is long
# enough to avoid busy polling.
DEFAULT_IDLE_INTERVAL = 3600.0


class SchedulerService:
    """Drive :class:`SchedulerStore.tick` from inside an asyncio event loop.

    This is the in-loop replacement for calling the scheduler tick from an
    external cron: the service sleeps precisely until the next schedule is due
    (no busy polling), fires the tick, and repeats. It cooperates with the
    daemon event loop (plan items 1.1/1.2) rather than owning it — a caller can
    ``spawn`` :meth:`run` as a task and ``stop`` / cancel it during shutdown.

    Time is fully injectable so tests use a controllable clock instead of real
    sleeps:

    - ``now`` supplies the current time (defaults to UTC ``datetime.now``).
    - ``sleep`` performs the actual waiting (defaults to :func:`asyncio.sleep`).
      A test can pass a fake ``sleep`` that advances a fake ``now`` and returns
      immediately, exercising many simulated hours in milliseconds.

    Backward compatibility: the persisted schedule format and
    :class:`SchedulerStore` semantics are unchanged; existing schedules are
    honoured as-is.

    Parameters
    ----------
    store:
        The schedule store to tick.
    task_queue:
        Queue that due schedules enqueue :class:`RuntimeTask` items into.
    now:
        Injectable clock returning a timezone-aware ``datetime``.
    sleep:
        Injectable awaitable sleep. Defaults to :func:`asyncio.sleep`.
    on_tick:
        Optional callback (sync or async) invoked with the
        :class:`ScheduleTickReport` whenever a tick enqueues at least one task.
        This is the hook the daemon loop uses to wake its dispatcher.
    limit:
        Optional per-tick cap forwarded to :meth:`SchedulerStore.tick`.
    idle_interval:
        Upper bound on a single sleep when no schedule is pending. Must be > 0.
    """

    def __init__(
        self,
        store: SchedulerStore,
        task_queue: TaskQueueStore,
        *,
        now: NowFn = _now,
        sleep: SleepFn | None = None,
        on_tick: TickCallback | None = None,
        limit: int | None = None,
        idle_interval: float = DEFAULT_IDLE_INTERVAL,
    ) -> None:
        if idle_interval <= 0:
            raise ValueError("idle_interval must be positive")
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0 or None")
        self._store = store
        self._task_queue = task_queue
        self._now = now
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._on_tick = on_tick
        self._limit = limit
        self._idle_interval = float(idle_interval)
        self._wake_event = asyncio.Event()
        self._stopped = False
        self._running = False
        self._ticks = 0

    # ── observability ────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """True while :meth:`run` is executing."""
        return self._running

    @property
    def stopped(self) -> bool:
        """True once :meth:`stop` has been requested."""
        return self._stopped

    @property
    def ticks(self) -> int:
        """Number of completed tick cycles that enqueued at least one task."""
        return self._ticks

    # ── control ──────────────────────────────────────────────────────────

    def notify(self) -> None:
        """Wake the service so it re-evaluates the next due time immediately."""
        self._wake_event.set()

    def stop(self) -> None:
        """Ask the service to exit after the current wait. Idempotent."""
        self._stopped = True
        self._wake_event.set()

    # ── scheduling maths ─────────────────────────────────────────────────

    def seconds_until_next(self, now: datetime | None = None) -> float | None:
        """Seconds until the earliest active schedule is due.

        Returns ``None`` when there is no active schedule (the caller should
        idle-wait), ``0.0`` or a negative value when something is already due.
        """
        moment = (now or self._now()).astimezone(timezone.utc)
        upcoming = [
            _parse_iso(schedule.next_run_at)
            for schedule in self._store.list(status="active")
        ]
        if not upcoming:
            return None
        return (min(upcoming) - moment).total_seconds()

    def _clamp_wait(self, delay: float | None) -> float:
        if delay is None:
            return self._idle_interval
        if delay <= 0:
            return 0.0
        return min(delay, self._idle_interval)

    # ── main loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Run until :meth:`stop` is called or the task is cancelled."""
        if self._running:
            raise RuntimeError("SchedulerService is already running")
        self._running = True
        logger.info(
            "scheduler service started (idle_interval=%.0fs)", self._idle_interval
        )
        try:
            while not self._stopped:
                wait = self._clamp_wait(self.seconds_until_next())
                if wait > 0:
                    await self._sleep_or_wake(wait)
                    continue
                report = self._store.tick(
                    task_queue=self._task_queue,
                    now=self._now(),
                    limit=self._limit,
                )
                if report.enqueued_count:
                    self._ticks += 1
                    logger.info(
                        "scheduler tick enqueued %d task(s): %s",
                        report.enqueued_count,
                        list(report.task_ids),
                    )
                    await self._emit(report)
                else:
                    # Nothing became due after all (e.g. the schedule was paused
                    # or removed by an out-of-band writer between our two reads).
                    # Yield, then loop: the next read recomputes a real wait, so
                    # this can never hot-spin.
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("scheduler service cancelled")
            raise
        finally:
            self._running = False
            logger.info("scheduler service stopped after %d tick(s)", self._ticks)

    async def _sleep_or_wake(self, delay: float) -> bool:
        """Sleep up to ``delay`` seconds or until notified/stopped."""
        if self._stopped or self._wake_event.is_set():
            self._wake_event.clear()
            return True
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waiter = asyncio.ensure_future(self._wake_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for future in (sleeper, waiter):
                if not future.done():
                    future.cancel()
            await asyncio.gather(sleeper, waiter, return_exceptions=True)
        woke = waiter in done
        if woke:
            self._wake_event.clear()
        return woke

    async def _emit(self, report: ScheduleTickReport) -> None:
        if self._on_tick is None:
            return
        try:
            result = self._on_tick(report)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler on_tick callback failed for %s", report)
