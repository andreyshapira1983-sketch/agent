"""Persistent task queue for autonomous runtime work.

The first autonomous runtime could run a bounded dry-run pass, but it had no
memory between launches. This queue is the durable handoff: scheduler ticks,
CLI commands, and future monitors can enqueue work; the runtime can claim a
pending task, run it, and record the result.
"""
from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from core.file_lock import exclusive_file_lock
from core.ids import new_id
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked

logger = logging.getLogger(__name__)

#: Where the queue lives. Defined HERE, beside the store that owns it,
#: rather than in `app/bootstrap.py`: the daemon needs the path and must
#: not pay for the agent graph to learn it — importing `app.bootstrap`
#: costs 365 ms and 404 modules, against 73 ms for this module, and
#: `agent_tick.py` lazily imports `build_agent` in three places precisely
#: to keep `--status` cheap. `app.bootstrap` re-exports this name, so the
#: block of default paths there still reads as one list.
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"

RuntimeTaskKind = Literal["auto_run", "resume_checkpoint"]
RuntimeTaskStatus = Literal[
    "pending", "running", "done", "failed", "cancelled", "paused", "blocked"
]
_VALID_KINDS = {"auto_run", "resume_checkpoint"}
_VALID_STATUSES = {
    "pending", "running", "done", "failed", "cancelled", "paused", "blocked",
}

class TaskAlreadyClaimed(RuntimeError):
    """Raised when a claim loses the race — the task is no longer `pending`.

    Distinct from ``KeyError`` on purpose: a task that is gone and a task
    somebody else is already running call for different reactions from a
    consumer, and both used to be indistinguishable because neither happened.
    """


#: A run that stopped because a human must approve something is neither a
#: success nor a failure — retrying it on a timer cannot help, and burning the
#: attempt budget on it hides the real reason. `blocked` is its own resting
#: state: invisible to `pending()`, visible in `summary()`, and left for the
#: operator (`:task-unblock` once the approval is resolved). See MIR-039.
_BLOCKED_STATUS: RuntimeTaskStatus = "blocked"

# Exponential backoff for re-queued failed tasks (OFM-010 / CORE-07): a
# deterministic failure must not be immediately eligible again on the next tick.
# attempts is already bumped in mark_running, so the first failure (attempts=1)
# waits BASE seconds, then doubles, capped at MAX.
_RETRY_BACKOFF_BASE_SECONDS = 30
_RETRY_BACKOFF_MAX_SECONDS = 3600
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


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
    #: Liveness, refreshed by the consumer *while the task runs* (see
    #: `core.task_lifecycle.task_heartbeat`). `updated_at` cannot serve this
    #: purpose: it is written once at `mark_running` and then stands still, so a
    #: task legitimately running for an hour is indistinguishable from one whose
    #: process was killed — the ambiguity that made the first attempt at wiring
    #: `recover_stuck` unsafe (MIR-040). Empty on rows written before this field
    #: existed; recovery falls back to `updated_at` for those.
    heartbeat_at: str = ""
    #: Who claimed the task. Diagnostics for the operator; recovery does not
    #: trust them (a pid can be reused), it trusts the heartbeat going stale.
    owner_pid: int = 0
    owner_host: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def liveness_at(self) -> str:
        """Timestamp recovery measures staleness against."""
        return self.heartbeat_at or self.updated_at

    @classmethod
    def from_dict(cls, data: dict) -> RuntimeTask:
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
            heartbeat_at=str(data.get("heartbeat_at") or ""),
            owner_pid=int(data.get("owner_pid") or 0),
            owner_host=str(data.get("owner_host") or ""),
        )

    def with_updates(self, **updates) -> RuntimeTask:
        data = self.to_dict()
        data.update(updates)
        data["updated_at"] = _iso()
        return RuntimeTask.from_dict(data)


def _failure_transition(
    task: RuntimeTask,
    *,
    error: str,
    report: dict | None = None,
    now: datetime,
) -> RuntimeTask:
    """The single decider for "this attempt did not succeed".

    Terminal ``failed`` once the attempt budget is spent, otherwise re-queued
    with exponential backoff. Both ``mark_failed`` (the run reported a failure)
    and ``recover_stuck`` (the run's process vanished) go through here, so the
    retry cap cannot be honoured on one path and bypassed on the other.
    """
    if task.attempts >= task.max_attempts:
        return task.with_updates(
            status="failed",
            last_error=error,
            last_report=report or task.last_report,
        )
    delay = min(
        _RETRY_BACKOFF_MAX_SECONDS,
        _RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, task.attempts - 1)),
    )
    return task.with_updates(
        status="pending",
        last_error=error,
        last_report=report or task.last_report,
        run_after=_iso(now + timedelta(seconds=delay)),
    )


TaskAddedCallback = Callable[[RuntimeTask], None]


class TaskQueueStore:
    """JSONL-backed runtime task queue.

    The file is rewritten on status updates. That keeps the current state easy
    to inspect by hand and avoids event-log compaction for this early slice.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        on_task_added: TaskAddedCallback | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._on_task_added = on_task_added
        #: Rows the last read could not parse. Zero is the normal case; anything
        #: else means queued work disappeared and somebody must look at the file.
        self.last_unreadable_rows = 0

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
        # Validated here, not only in `from_dict`: an unknown kind used to be
        # accepted, written to disk, and then dropped by every later read —
        # the caller saw a task object and the work never ran (2026-08-04).
        kind = _choice(  # type: ignore[assignment]
            kind, default="auto_run", allowed=_VALID_KINDS, field_name="kind",
        )
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
        self._notify_task_added(task)
        return task

    def add_paused_checkpoint(
        self,
        *,
        goal: str,
        report: dict,
        priority: int = 1,
    ) -> RuntimeTask:
        task = RuntimeTask(
            kind="resume_checkpoint",
            goal=goal.strip() or "resume interrupted task",
            status="paused",
            priority=priority,
            max_attempts=1,
            dry_run=True,
            include_tests=False,
            limit=1,
            learning_limit=1,
            last_error=str(report.get("stop_reason") or "budget_exhausted"),
            last_report=report,
        )
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            tasks.append(task)
            self._save_unlocked(tasks)
        self._notify_task_added(task)
        return task

    def _notify_task_added(self, task: RuntimeTask) -> None:
        """Notify the daemon after a new task is durably visible in the queue."""
        if self._on_task_added is None:
            return
        try:
            self._on_task_added(task)
        except Exception:
            logger.exception("runtime task wake callback failed for %s", task.id)

    def load(self) -> list[RuntimeTask]:
        with exclusive_file_lock(self._lock_path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[RuntimeTask]:
        if not self.path.exists():
            # The counter describes THIS read, so the no-file path has to clear
            # it too. Left alone, a queue that was rotated away would keep
            # reporting the dropped rows of the read before it — a number about
            # data that is no longer there, which is the very failure this
            # counter exists to make visible.
            self.last_unreadable_rows = 0
            return []
        tasks: list[RuntimeTask] = []
        unreadable = 0
        for raw in read_state_jsonl_unlocked(self.path):
            try:
                tasks.append(RuntimeTask.from_dict(raw))
            except (TypeError, ValueError) as exc:
                # Skipping stays — one bad row must not sink the queue. Silence
                # does not: this is a task nobody will ever run again.
                unreadable += 1
                fields = raw if isinstance(raw, dict) else {}
                logger.warning(
                    "runtime task row dropped (%s): id=%s kind=%s",
                    exc, fields.get("id", "?"), fields.get("kind", "?"),
                )
        self.last_unreadable_rows = unreadable
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

    def mark_running(
        self,
        task_id: str,
        *,
        owner_pid: int | None = None,
        owner_host: str | None = None,
    ) -> RuntimeTask:
        """Claim a pending task. Exclusive: a second claimant is refused.

        The check runs inside `_update_one`'s file lock, on the row as just read
        from disk, so the test and the write are one transaction. Without it two
        consumers both claimed the same task — measured with two processes: the
        second claim burned an attempt and overwrote `owner_pid`, so the row no
        longer named the process that was actually running it.

        The single-instance lock is not enough on its own: `agent_tick` takes it,
        but `:task-run` and `:schedule-tick --run` do not.
        """
        pid = os.getpid() if owner_pid is None else int(owner_pid)
        host = socket.gethostname() if owner_host is None else str(owner_host)

        def claim(task: RuntimeTask) -> RuntimeTask:
            if task.status != "pending":
                raise TaskAlreadyClaimed(
                    f"task {task.id} is {task.status}, not pending"
                )
            return task.with_updates(
                status="running",
                attempts=task.attempts + 1,
                last_error="",
                heartbeat_at=_iso(),
                owner_pid=pid,
                owner_host=host,
            )

        return self._update_one(task_id, claim)

    def heartbeat(self, task_id: str) -> RuntimeTask | None:
        """Refresh liveness for a task that is still running.

        Returns the updated task, or ``None`` when the task is gone or no longer
        ``running`` — a heartbeat must never resurrect a finished task, and a
        consumer whose heartbeat thread outlives the run must not be able to
        keep a stale row looking alive.
        """
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            out: list[RuntimeTask] = []
            updated: RuntimeTask | None = None
            for task in tasks:
                if task.id == task_id and task.status == "running":
                    updated = task.with_updates(heartbeat_at=_iso())
                    out.append(updated)
                else:
                    out.append(task)
            if updated is None:
                return None
            self._save_unlocked(out)
            return updated

    def mark_blocked(
        self,
        task_id: str,
        *,
        reason: str,
        report: dict | None = None,
    ) -> RuntimeTask:
        """Park a task that cannot proceed without a human decision.

        Distinct from ``failed`` on purpose: nothing went wrong, and no retry
        schedule can unblock it. It leaves ``pending()`` (so no consumer picks
        it up) and waits for the operator.
        """
        return self._update_one(
            task_id,
            lambda task: task.with_updates(
                status=_BLOCKED_STATUS,
                last_error=reason,
                last_report=report or task.last_report,
            ),
        )

    def unblock(self, task_id: str, *, now: datetime | None = None) -> RuntimeTask:
        """Return a blocked task to the queue once the human decision is made.

        Raises ``ValueError`` when the task is not blocked: this is the operator
        undoing a specific, visible state, not a generic status setter.
        """
        def update(task: RuntimeTask) -> RuntimeTask:
            if task.status != _BLOCKED_STATUS:
                raise ValueError(
                    f"task {task.id} is {task.status}, not blocked"
                )
            return task.with_updates(
                status="pending",
                last_error="",
                run_after=_iso(now),
            )

        return self._update_one(task_id, update)

    def mark_done(self, task_id: str, *, report: dict | None = None) -> RuntimeTask:
        return self._update_one(
            task_id,
            lambda task: task.with_updates(
                status="done",
                last_report=report or task.last_report,
                last_error="",
            ),
        )

    def mark_failed(
        self,
        task_id: str,
        *,
        error: str,
        report: dict | None = None,
        now: datetime | None = None,
    ) -> RuntimeTask:
        retry_from = (now or _now()).astimezone(timezone.utc)
        # Exponential backoff so a deterministic failure does not hot-retry
        # every tick (OFM-010 / CORE-07); shared with recovery so the retry cap
        # holds on both paths.
        return self._update_one(
            task_id,
            lambda task: _failure_transition(
                task, error=error, report=report, now=retry_from
            ),
        )

    def cancel(self, task_id: str) -> RuntimeTask:
        return self._update_one(task_id, lambda task: task.with_updates(status="cancelled"))

    def recover_stuck(
        self,
        *,
        timeout_minutes: int = 30,
        now: datetime | None = None,
    ) -> list[RuntimeTask]:
        """Finalise tasks left ``running`` by a process that died mid-run.

        A task is orphaned when its **heartbeat** (``heartbeat_at``, falling
        back to ``updated_at`` for rows written before heartbeats existed) is
        older than *timeout_minutes*. Staleness is a liveness signal only
        because the consumer refreshes it while the task runs; that is the whole
        point of :func:`core.task_lifecycle.task_heartbeat`.

        Recovery is routed through the ordinary failure policy rather than
        resetting straight to ``pending``:

        * a task with attempts left is re-queued with the standard exponential
          backoff, so a task that kills its process is not hot-retried;
        * a task that has exhausted ``max_attempts`` becomes terminal
          ``failed`` — the earlier version resurrected it and ran one attempt
          past the cap (MIR-040).

        **Caller contract.** This must run only where no consumer can be
        holding a task in flight — at startup, under the single-instance lock.
        :func:`core.task_lifecycle.recover_orphaned_tasks` enforces that; call
        it rather than this method. Called concurrently with a live consumer,
        this reclaims a task that is still executing and two processes run the
        same work.
        """
        moment = (now or _now()).astimezone(timezone.utc)
        cutoff_ts = moment.timestamp() - timeout_minutes * 60
        recovered: list[RuntimeTask] = []
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            out: list[RuntimeTask] = []
            for task in tasks:
                if task.status != "running":
                    out.append(task)
                    continue
                try:
                    live_ts = _parse_iso(task.liveness_at()).timestamp()
                except (ValueError, AttributeError, TypeError):
                    live_ts = 0.0  # unparseable → treat as very old
                if live_ts >= cutoff_ts:
                    out.append(task)  # still beating: leave it alone
                    continue
                error = (
                    f"orphaned: no heartbeat for over {timeout_minutes} min "
                    f"(owner pid={task.owner_pid or '?'} "
                    f"host={task.owner_host or '?'})"
                )
                fixed = _failure_transition(task, error=error, now=moment)
                out.append(fixed)
                recovered.append(fixed)
            if recovered:
                self._save_unlocked(out)
        return recovered

    def summary(self) -> dict:
        tasks = self.load()
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        resumable = []
        for task in tasks:
            if task.kind != "resume_checkpoint" or task.status != "paused":
                continue
            report = task.last_report or {}
            resumable.append(
                {
                    "id": task.id,
                    "goal": task.goal,
                    "trace_id": report.get("trace_id"),
                    "stop_reason": report.get("stop_reason"),
                    "current_phase": report.get("current_phase"),
                    "updated_at": task.updated_at,
                }
            )
        return {
            "path": str(self.path),
            "total": len(tasks),
            "statuses": counts,
            "pending_due": len(self.pending()),
            "resumable": resumable,
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
