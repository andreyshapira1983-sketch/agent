"""Persistent task queue for autonomous runtime work."""
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
from core.record_fields import checked_iso_field as _iso_field
from core.record_fields import parse_utc_iso as _parse_iso
from core.record_fields import strict_bool as _bool
from core.record_fields import utc_iso as _iso
from core.record_fields import utc_now as _now
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked

logger = logging.getLogger(__name__)

#: Where the queue lives; defined here so the daemon can learn the path without importing the agent graph; `app.bootstrap` re-exports it.
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"

#: The only path with a human at the turn; every other path (`runtime`, `daemon`, `self_apply`, `cli`) is unattended, and a new path lands here too: the queue prefers keeping extra work to losing it.
INTERACTIVE_GATEWAY_PATH = "repl"


def checkpoint_is_resumable_work(gateway_path: str | None) -> bool:
    """Whether an interrupted turn should be parked as work."""
    return str(gateway_path or INTERACTIVE_GATEWAY_PATH) != INTERACTIVE_GATEWAY_PATH


RuntimeTaskKind = Literal["auto_run", "resume_checkpoint"]
RuntimeTaskStatus = Literal[
    "pending", "running", "done", "failed", "cancelled", "paused", "blocked"
]
_VALID_KINDS = {"auto_run", "resume_checkpoint"}
_VALID_STATUSES = {
    "pending", "running", "done", "failed", "cancelled", "paused", "blocked",
}

class TaskAlreadyClaimed(RuntimeError):
    """Raised when a claim loses the race — the task is no longer `pending`."""


class TerminalOutcomeRewrite(RuntimeError):
    """Raised when a settled outcome would be replaced by a DIFFERENT one (e.g. `failed -> done`); repeating the SAME outcome stays idempotent."""


#: Outcomes after which the work is over; `blocked` waits for a human and `paused` for a clock, so both stay rewritable.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled"})


#: A run that stopped because a human must approve something is neither success nor failure; `blocked` is its own resting state: invisible to `pending()`, visible in `summary()`, cleared by `:task-unblock`.
_BLOCKED_STATUS: RuntimeTaskStatus = "blocked"

#: Stop reasons a CLOCK can clear; the only pauses `reactivate_paused_checkpoints` returns to the queue (`budget_kill_switch` is a decision and is NOT here); matched on prefix because the campaign appends detail.
_CLOCK_CLEARABLE_STOPS: tuple[str, ...] = ("budget_exhausted", "budget_wait")

#: Upper bound on how old a parked checkpoint may be and still be resumed; AGE, not content, because an older checkpoint describes a world that no longer exists.
_MAX_RESUMABLE_AGE_HOURS = 72

#: How many parked rows one reactivation pass may revive; the tick's drain loop is unbounded, so this bounds a salvo on old debt.
_REACTIVATION_BATCH = 3


def _is_resource_paused(task: RuntimeTask) -> bool:
    """Is this row parked on a resource that refills, rather than on a person?"""
    if task.kind != "resume_checkpoint" or task.status != "paused":
        return False
    reason = str(
        (task.last_report or {}).get("stop_reason") or task.last_error or ""
    ).strip()
    return reason.startswith(_CLOCK_CLEARABLE_STOPS)

# Exponential backoff for re-queued failed tasks: attempts is already bumped in mark_running, so the first failure waits BASE seconds, then doubles, capped at MAX.
_RETRY_BACKOFF_BASE_SECONDS = 30
_RETRY_BACKOFF_MAX_SECONDS = 3600


def _choice(value: object, *, default: str, allowed: set[str], field_name: str) -> str:
    out = str(value or default)
    if out not in allowed:
        raise ValueError(f"invalid {field_name}: {out}")
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
    #: Liveness, refreshed by the consumer while the task runs (`core.task_lifecycle.task_heartbeat`); `updated_at` stands still after `mark_running`. Empty on old rows; recovery falls back to `updated_at`.
    heartbeat_at: str = ""
    #: Who claimed the task; diagnostics only (a pid can be reused), recovery trusts the stale heartbeat.
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
    """The single decider for "this attempt did not succeed"."""
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
        #: Rows the last read could not parse: queued work that will not run until the file is fixed; the save writes them back.
        self.last_unreadable_rows = 0
        self._unparsed_rows: list[dict] = []

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
        # validated here, not only in `from_dict`: an unknown kind would be written and then dropped by every later read
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
        gateway_path: str | None = None,
        resumed_from: str | None = None,
    ) -> RuntimeTask:
        """Park interrupted work so it can be picked up again.

        ``gateway_path`` is stored because :func:`checkpoint_is_resumable_work` decides by it at park time.
        ``resumed_from`` carries the SAME row forward instead of adding a second one, so one piece of work keeps one row
        (a resume that pauses again must not be retired; see `tests/test_budget_resume.py`).
        """
        payload = dict(report)
        if gateway_path is not None:
            payload["gateway_path"] = str(gateway_path)
        if resumed_from:
            carried = self._carry_checkpoint_forward(resumed_from, payload)
            if carried is not None:
                return carried
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
            last_error=str(payload.get("stop_reason") or "budget_exhausted"),
            last_report=payload,
        )
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            tasks.append(task)
            self._save_unlocked(tasks)
        self._notify_task_added(task)
        return task

    def _carry_checkpoint_forward(
        self, resumed_from: str, payload: dict
    ) -> RuntimeTask | None:
        """Fold a fresh stop into the paused row it came from, if one exists."""
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            out: list[RuntimeTask] = []
            carried: RuntimeTask | None = None
            for task in tasks:
                same_work = (
                    carried is None
                    and task.kind == "resume_checkpoint"
                    and task.status == "paused"
                    and (task.last_report or {}).get("trace_id") == resumed_from
                )
                if not same_work:
                    out.append(task)
                    continue
                carried = task.with_updates(
                    last_report=payload,
                    last_error=str(payload.get("stop_reason") or task.last_error),
                )
                out.append(carried)
            if carried is not None:
                self._save_unlocked(out)
            return carried

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
            # the counter describes THIS read: a rotated-away queue must not keep reporting old rows
            self.last_unreadable_rows = 0
            self._unparsed_rows = []
            return []
        tasks: list[RuntimeTask] = []
        unparsed: list[dict] = []
        for raw in read_state_jsonl_unlocked(self.path):
            try:
                tasks.append(RuntimeTask.from_dict(raw))
            except (TypeError, ValueError) as exc:
                # skipping stays (one bad row must not sink the queue), silence does not
                unparsed.append(raw)
                logger.warning(
                    "runtime task row kept, not run (%s): id=%s kind=%s",
                    exc, raw.get("id", "?"), raw.get("kind", "?"),
                )
        self.last_unreadable_rows = len(unparsed)
        self._unparsed_rows = unparsed
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
        """Claim a pending task. Exclusive: a second claimant is refused."""
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
        """Refresh liveness for a task that is still running."""
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
        """Park a task that cannot proceed without a human decision."""
        return self._update_one(
            task_id,
            lambda task: task.with_updates(
                status=_BLOCKED_STATUS,
                last_error=reason,
                last_report=report or task.last_report,
            ),
        )

    def unblock(self, task_id: str, *, now: datetime | None = None) -> RuntimeTask:
        """Return a blocked task to the queue once the human decision is made; raises ``ValueError`` when the task is not blocked."""
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

    def _refuse_terminal_rewrite(self, task_id: str, wanted: str) -> None:
        """Refuse when the outcome is already recorded and is a DIFFERENT one."""
        for task in self.list():
            if task.id != task_id:
                continue
            if task.status in _TERMINAL_STATUSES and task.status != wanted:
                raise TerminalOutcomeRewrite(
                    f"task {task_id} already settled as {task.status}; "
                    f"refusing to rewrite it as {wanted}"
                )
            return

    def mark_done(self, task_id: str, *, report: dict | None = None) -> RuntimeTask:
        self._refuse_terminal_rewrite(task_id, "done")
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
        self._refuse_terminal_rewrite(task_id, "failed")
        retry_from = (now or _now()).astimezone(timezone.utc)
        # exponential backoff so a deterministic failure does not hot-retry every tick; shared with recovery
        return self._update_one(
            task_id,
            lambda task: _failure_transition(
                task, error=error, report=report, now=retry_from
            ),
        )

    def cancel(self, task_id: str) -> RuntimeTask:
        self._refuse_terminal_rewrite(task_id, "cancelled")
        return self._update_one(task_id, lambda task: task.with_updates(status="cancelled"))

    def reactivate_paused_checkpoints(
        self,
        *,
        cooldown_minutes: int = 60,
        now: datetime | None = None,
    ) -> list[RuntimeTask]:
        """Return work parked by a REPLENISHING resource to the queue.

        The exit condition must match the entry condition: a checkpoint parked by an exhausted budget waits on a clock, not a human.
        Narrow on purpose: only `resume_checkpoint` rows whose stop reason a clock can clear; only after *cooldown_minutes*;
        never past `max_attempts` (such a row becomes terminal `failed`). Caller contract is `recover_stuck`'s: startup only,
        under the single-instance lock; call :func:`core.task_lifecycle.reactivate_resumable_work` rather than this.
        """
        moment = (now or _now()).astimezone(timezone.utc)
        cutoff_ts = moment.timestamp() - cooldown_minutes * 60

        def _parked_ts(task: RuntimeTask) -> float:
            try:
                return _parse_iso(task.updated_at).timestamp()
            except (ValueError, AttributeError, TypeError):
                return 0.0  # unparseable → treat as long past

        changed: list[RuntimeTask] = []
        with exclusive_file_lock(self._lock_path):
            tasks = self._load_unlocked()
            stale_ts = moment.timestamp() - _MAX_RESUMABLE_AGE_HOURS * 3600
            due = [
                t for t in tasks
                if _is_resource_paused(t) and _parked_ts(t) <= cutoff_ts
            ]
            # too old to mean anything: terminal rather than left `paused`
            stale_ids = {t.id for t in due if _parked_ts(t) < stale_ts}
            due = [t for t in due if t.id not in stale_ids]
            # batch bound, oldest first: the drain loop runs everything pending in one pass; the rest stay paused
            revivable = [t for t in due if t.attempts < t.max_attempts]
            revive_ids = {
                t.id for t in sorted(revivable, key=_parked_ts)[:_REACTIVATION_BATCH]
            }
            out: list[RuntimeTask] = []
            for task in tasks:
                if task.id in stale_ids:
                    expired = task.with_updates(
                        status="failed",
                        last_error=(
                            f"{task.last_error or 'paused'}; stale checkpoint "
                            f"older than {_MAX_RESUMABLE_AGE_HOURS}h"
                        ),
                    )
                    out.append(expired)
                    changed.append(expired)
                elif task.id in revive_ids:
                    revived = task.with_updates(
                        status="pending", run_after=_iso(moment)
                    )
                    out.append(revived)
                    changed.append(revived)
                elif task in due and task.attempts >= task.max_attempts:
                    spent = task.with_updates(
                        status="failed",
                        last_error=(
                            f"{task.last_error or 'paused'}; no attempts left "
                            f"({task.attempts}/{task.max_attempts})"
                        ),
                    )
                    out.append(spent)
                    changed.append(spent)
                else:
                    out.append(task)
            if changed:
                self._save_unlocked(out)
        return changed

    def recover_stuck(
        self,
        *,
        timeout_minutes: int = 30,
        now: datetime | None = None,
        finalise_exhausted: bool = True,
    ) -> list[RuntimeTask]:
        """Finalise tasks left ``running`` by a process that died mid-run.

        A task is orphaned when its heartbeat (falling back to ``updated_at``) is older than *timeout_minutes*.
        Recovery goes through the ordinary failure policy: attempts left means re-queued with exponential backoff;
        exhausted ``max_attempts`` means terminal ``failed``.

        Caller contract: run only where no consumer can hold a task in flight (startup, single-instance lock);
        call :func:`core.task_lifecycle.recover_orphaned_tasks` rather than this. ``finalise_exhausted=False`` leaves an
        orphan past its cap ``running`` (dry tick): re-queueing is reversible, the terminal verdict is not.
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
                if not finalise_exhausted and task.attempts >= task.max_attempts:
                    out.append(task)  # dry pass: no verdict it cannot take back
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
        rewrite_state_jsonl_unlocked(
            self.path, [task.to_dict() for task in tasks] + self._unparsed_rows
        )
