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

#: Where the queue lives. Defined HERE, beside the store that owns it,
#: rather than in `app/bootstrap.py`: the daemon needs the path and must
#: not pay for the agent graph to learn it — importing `app.bootstrap`
#: costs 365 ms and 404 modules, against 73 ms for this module, and
#: `agent_tick.py` lazily imports `build_agent` in three places precisely
#: to keep `--status` cheap. `app.bootstrap` re-exports this name, so the
#: block of default paths there still reads as one list.
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"

#: Единственный путь, на котором за ходом сидит человек. Всё остальное —
#: `runtime`, `daemon`, `self_apply`, `cli` — работа без присмотра, и новый путь
#: попадёт сюда же: очередь скорее сохранит лишнее, чем потеряет работу.
INTERACTIVE_GATEWAY_PATH = "repl"


def checkpoint_is_resumable_work(gateway_path: str | None) -> bool:
    """Стоит ли парковать прерванный ход как работу."""
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
    """Raised when a settled outcome would be replaced by a DIFFERENT one.

    H-21 in `docs/audit/HISTORICAL_FAILURE_LEDGER.md` — the state machine that
    accepts a transition its own diagram does not have. Measured 2026-08-24:
    every terminal state could become any other, so `failed -> done` silently
    turned a failure into a success and everything counting by status —
    the cycle report, useful-cycles, the capability bench — counted the
    rewrite. Repeating the SAME outcome stays idempotent; callers rely on it.
    """


#: Исходы, после которых работа кончилась. `blocked` ждёт человека, `paused` —
#: часов; оба не терминальны и обязаны оставаться переписываемыми.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled"})


#: A run that stopped because a human must approve something is neither a
#: success nor a failure — retrying it on a timer cannot help, and burning the
#: attempt budget on it hides the real reason. `blocked` is its own resting
#: state: invisible to `pending()`, visible in `summary()`, and left for the
#: operator (`:task-unblock` once the approval is resolved). See MIR-039.
_BLOCKED_STATUS: RuntimeTaskStatus = "blocked"

#: Stop reasons a CLOCK can clear without anyone deciding anything. These are
#: the only pauses :meth:`TaskQueueStore.reactivate_paused_checkpoints` will
#: return to the queue, and the list is deliberately short rather than a
#: `startswith("budget")`: `budget_kill_switch` is also budget-shaped and is
#: NOT here, because a kill switch is a decision somebody made and waiting does
#: not undo it. Everything absent from this set keeps the human-only exit.
#: Matched on prefix because the campaign path appends detail —
#: `core/campaign.py:193` writes `budget_exhausted:llm_calls=3/3`.
_CLOCK_CLEARABLE_STOPS: tuple[str, ...] = ("budget_exhausted", "budget_wait")

#: Upper bound on how old a parked checkpoint may be and still be resumed.
#: The ladder had only a LOWER bound (the cooldown), and the live store on
#: 2026-08-22 showed what that costs: fourteen rows stopped by an exhausted
#: budget between 2026-07-30 and 2026-08-15, revived oldest-first, three per
#: pass — so the first hours of an unattended week would have gone to
#: re-answering July's chat (one goal was literally «Answer the question:
#: привет»), ahead of any work the agent would have chosen itself, because the
#: queue step of the tick runs before the self-directed producer.
#:
#: The bound is AGE, not content: judging a goal's worth by its text is
#: guessing, while age is a fact — a checkpoint older than this describes a
#: world that no longer exists (the files moved, the budget window is another
#: one, the conversation that produced the question ended weeks ago).
_MAX_RESUMABLE_AGE_HOURS = 72

#: How many parked rows one reactivation pass may revive. The tick's drain
#: loop is unbounded, so this is the only thing standing between a backlog and
#: a salvo that spends the whole refilled window on old debt.
_REACTIVATION_BATCH = 3


def _is_resource_paused(task: RuntimeTask) -> bool:
    """Is this row parked on a resource that refills, rather than on a person?"""
    if task.kind != "resume_checkpoint" or task.status != "paused":
        return False
    reason = str(
        (task.last_report or {}).get("stop_reason") or task.last_error or ""
    ).strip()
    return reason.startswith(_CLOCK_CLEARABLE_STOPS)

# Exponential backoff for re-queued failed tasks (OFM-010 / CORE-07): a
# deterministic failure must not be immediately eligible again on the next tick.
# attempts is already bumped in mark_running, so the first failure (attempts=1)
# waits BASE seconds, then doubles, capped at MAX.
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
        gateway_path: str | None = None,
        resumed_from: str | None = None,
    ) -> RuntimeTask:
        """Park interrupted work so it can be picked up again.

        ``gateway_path`` is stored because :func:`checkpoint_is_resumable_work`
        decides by it AT PARK TIME and nothing kept the evidence: all fourteen
        rows in the live store on 2026-08-22 carried no trace of who produced
        them, so nothing later could judge whether resuming them unattended was
        right. The decision is recorded beside its own grounds.

        ``resumed_from`` carries the SAME row forward instead of adding a second
        one. Without it, switching reactivation on turns a static set into
        unbounded growth: a resume that hits the budget again parks its own
        checkpoint, and `retire_paused_checkpoint` only fires on success (see
        `tests/test_budget_resume.py::test_resume_that_pauses_again_keeps_the_old_task`,
        which holds that an unfinished pause may not be retired). Carrying the
        row forward keeps that invariant — the id survives, still paused — while
        one piece of work keeps exactly one row.
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

    def _refuse_terminal_rewrite(self, task_id: str, wanted: str) -> None:
        """Отказать, если исход уже записан и он ДРУГОЙ."""
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
        self._refuse_terminal_rewrite(task_id, "cancelled")
        return self._update_one(task_id, lambda task: task.with_updates(status="cancelled"))

    def reactivate_paused_checkpoints(
        self,
        *,
        cooldown_minutes: int = 60,
        now: datetime | None = None,
    ) -> list[RuntimeTask]:
        """Return work parked by a REPLENISHING resource to the queue.

        The queue has two resting states that look alike and are not. `blocked`
        waits on a human decision: its own comment says retrying it on a timer
        cannot help, and it leaves by :meth:`unblock` at the operator's word.
        That is right. A checkpoint parked by an exhausted budget waits on
        something a clock resolves by itself, and giving it the same human-only
        exit is what stranded fourteen rows in the live store — the oldest since
        2026-07-30, every one at `attempts=0/1`, none ever seen by `pending()`
        again. **The exit condition must match the entry condition.**

        Deliberately narrow, on three axes:

        * only `resume_checkpoint` rows whose stop reason is one a clock can
          clear (the `budget_` family). A pause for any other reason keeps the
          human-only exit, so this repair cannot creep into `blocked`'s
          territory;
        * only after *cooldown_minutes*, because the single attempt these rows
          carry must not be spent while the window is still dry;
        * never past `max_attempts` — MIR-040 was earned when an earlier
          recovery resurrected a task one attempt beyond its cap. A checkpoint
          with nothing left becomes terminal `failed` rather than staying
          paused, because a row that can never run again must not keep
          advertising itself as resumable in `summary()`.

        Caller contract is `recover_stuck`'s: startup only, under the
        single-instance lock. :func:`core.task_lifecycle.reactivate_resumable_work`
        enforces it; call that rather than this.
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
            # Too old to mean anything. Terminal rather than left `paused`,
            # for the same reason the exhausted-attempts branch below is: a row
            # that will never run again must stop advertising itself as
            # resumable in `summary()`.
            stale_ids = {t.id for t in due if _parked_ts(t) < stale_ts}
            due = [t for t in due if t.id not in stale_ids]
            # Batch bound, oldest first: the tick's drain loop runs EVERYTHING
            # pending in one pass, so an unbounded revival would spend the
            # refilled window on backlog in one salvo (14 rows were waiting
            # when this was built). The rest stay paused for later ticks.
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

        ``finalise_exhausted=False`` keeps the second bullet from happening:
        an orphan past its cap is left ``running`` for a later pass instead of
        being buried. That is what a dry tick asks for. The narrow line was
        drawn by measurement (burn-in review, 2026-09-17): the attempt itself
        is spent by :meth:`mark_running`, and :func:`_failure_transition` never
        spends one — it either re-queues with backoff, which is reversible, or
        issues the terminal verdict, which is not. A pass that applies no
        effects may do the first and may not do the second.
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
        rewrite_state_jsonl_unlocked(self.path, [task.to_dict() for task in tasks])
