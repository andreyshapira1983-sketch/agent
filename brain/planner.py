"""
brain/planner.py — Task Planner (Decomposition & Execution Tracking)

The Planner breaks a goal into ordered steps and tracks their execution.

Key principle:
    Planner is PURE LOGIC — no LLM, no I/O, no side effects.
    Brain generates steps (via LLM), then hands them to Planner.
    Planner manages ORDER, DEPENDENCIES, and STATUS.

Concepts:
    Step   — one atomic action ("search web", "write file", "call API")
    Plan   — ordered list of Steps for one Goal
    Planner— manages active Plan, decides what's next, detects failures

Flow:
    Brain.think()
        → goal too complex for one LLM call
        → Brain asks LLM to decompose → gets list of step dicts
        → Brain calls Planner.create_plan(steps)
        → loop: Planner.next_step() → Brain executes → Planner.mark_done()
        → Planner.is_complete() → Brain finishes
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enums & Dataclasses
# ------------------------------------------------------------------

class StepStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    SKIPPED   = "skipped"


class PlanStatus(str, Enum):
    ACTIVE    = "active"
    COMPLETE  = "complete"
    FAILED    = "failed"
    ABORTED   = "aborted"


@dataclass
class Step:
    """
    One atomic action inside a Plan.
    Brain executes steps — Planner only tracks their state.
    """
    id: int
    description: str
    action: str                        # e.g. "search", "write_file", "call_api"
    params: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)  # step IDs that must be DONE first
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def start(self) -> None:
        self.status = StepStatus.RUNNING
        self.started_at = datetime.utcnow()

    def complete(self, result: Any = None) -> None:
        self.status = StepStatus.DONE
        self.result = result
        self.finished_at = datetime.utcnow()
        logger.info("[Step] Done | id=%d action=%s", self.id, self.action)

    def fail(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.error = error
        self.finished_at = datetime.utcnow()
        logger.warning("[Step] Failed | id=%d error=%s", self.id, error)

    def skip(self) -> None:
        self.status = StepStatus.SKIPPED
        self.finished_at = datetime.utcnow()
        logger.info("[Step] Skipped | id=%d", self.id)

    def is_ready(self, done_ids: set[int]) -> bool:
        """True if all dependencies are satisfied."""
        return all(dep in done_ids for dep in self.depends_on)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "action": self.action,
            "params": self.params,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "error": self.error,
        }


@dataclass
class Plan:
    """
    A structured sequence of Steps for one Goal.
    """
    goal: str
    steps: list[Step] = field(default_factory=list)
    status: PlanStatus = PlanStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.utcnow)

    def done_ids(self) -> set[int]:
        return {s.id for s in self.steps if s.status == StepStatus.DONE}

    def failed_ids(self) -> set[int]:
        return {s.id for s in self.steps if s.status == StepStatus.FAILED}

    def pending_steps(self) -> list[Step]:
        return [s for s in self.steps if s.status == StepStatus.PENDING]

    def ready_steps(self) -> list[Step]:
        """Steps whose dependencies are all satisfied."""
        done = self.done_ids()
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING and s.is_ready(done)
        ]

    def is_complete(self) -> bool:
        """True when no step is PENDING or RUNNING (all are in terminal state)."""
        return all(
            s.status not in (StepStatus.PENDING, StepStatus.RUNNING)
            for s in self.steps
        )

    def has_blocking_failure(self) -> bool:
        """True if a failed step blocks remaining steps."""
        failed = self.failed_ids()
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                for dep in step.depends_on:
                    if dep in failed:
                        return True
        return False

    def progress(self) -> dict:
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        return {
            "total": total,
            "done": done,
            "percent": round(done / total * 100) if total > 0 else 0,
        }

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "status": self.status.value,
            "progress": self.progress(),
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        """Rehydrate a Plan from `to_dict()` output.

        Used by `PlanCheckpointStore.load` to resume a job after a crash.
        Step status is preserved so the next call to `Planner.next_step()`
        skips already-DONE steps and picks up where the agent left off.
        """
        steps: list[Step] = []
        for raw in data.get("steps", []):
            step = Step(
                id=int(raw.get("id", 0)),
                description=str(raw.get("description", "")),
                action=str(raw.get("action", "unknown")),
                params=dict(raw.get("params") or {}),
                depends_on=list(raw.get("depends_on") or []),
                status=StepStatus(raw.get("status", "pending")),
                error=raw.get("error"),
            )
            steps.append(step)
        plan = cls(
            goal=str(data.get("goal", "")),
            steps=steps,
            status=PlanStatus(data.get("status", "active")),
        )
        ts = data.get("created_at")
        if ts:
            try:
                plan.created_at = datetime.fromisoformat(ts)
            except ValueError:
                pass
        return plan


# ------------------------------------------------------------------
# Planner
# ------------------------------------------------------------------

class Planner:
    """
    Manages the active Plan for the Brain.

    Brain creates a plan → Planner tracks it.
    Brain asks "what's next?" → Planner returns ready steps.
    Brain finishes a step → Planner updates state.
    Planner detects completion or failure automatically.

    One Planner per Brain instance.
    One active Plan at a time (previous plan is archived).
    """

    def __init__(self) -> None:
        self._active_plan: Plan | None = None
        self._history: list[Plan] = []
        logger.info("[Planner] Initialized")

    # ------------------------------------------------------------------
    # Plan lifecycle
    # ------------------------------------------------------------------

    def create_plan(self, goal: str, steps: list[dict]) -> Plan:
        """
        Brain calls this with a list of step dicts from LLM decomposition.

        Each step dict must have:
            - description: str
            - action: str
            - params: dict (optional)
            - depends_on: list[int] (optional, step IDs)

        Returns the created Plan.
        """
        if self._active_plan and self._active_plan.status == PlanStatus.ACTIVE:
            logger.warning("[Planner] Archiving previous active plan for goal='%s'", self._active_plan.goal)
            self._active_plan.status = PlanStatus.ABORTED
            self._history.append(self._active_plan)

        parsed_steps = []
        for i, raw in enumerate(steps):
            step = Step(
                id=i,
                description=raw.get("description", f"Step {i}"),
                action=raw.get("action", "unknown"),
                params=raw.get("params", {}),
                depends_on=raw.get("depends_on", []),
            )
            parsed_steps.append(step)

        plan = Plan(goal=goal, steps=parsed_steps)
        self._active_plan = plan

        logger.info(
            "[Planner] Plan created | goal='%s' steps=%d",
            goal, len(parsed_steps),
        )
        return plan

    def abort(self) -> None:
        """Cancel the current plan."""
        if self._active_plan:
            self._active_plan.status = PlanStatus.ABORTED
            self._history.append(self._active_plan)
            self._active_plan = None
            logger.info("[Planner] Plan aborted")

    # ------------------------------------------------------------------
    # Step execution interface
    # ------------------------------------------------------------------

    def next_step(self) -> Step | None:
        """
        Returns the next Step Brain should execute.
        Returns None if plan is complete, failed, or no steps ready.
        """
        if not self._active_plan:
            return None

        plan = self._active_plan

        # Check for completion
        if plan.is_complete():
            plan.status = PlanStatus.COMPLETE
            self._history.append(plan)
            self._active_plan = None
            logger.info("[Planner] Plan complete | goal='%s'", plan.goal)
            return None

        # Check for blocking failure
        if plan.has_blocking_failure():
            plan.status = PlanStatus.FAILED
            self._history.append(plan)
            self._active_plan = None
            logger.error("[Planner] Plan failed — blocking dependency | goal='%s'", plan.goal)
            return None

        ready = plan.ready_steps()
        if not ready:
            logger.debug("[Planner] No steps ready yet (waiting for dependencies)")
            return None

        # Return the first ready step and mark it running
        step = ready[0]
        step.start()
        logger.info("[Planner] Next step | id=%d action=%s", step.id, step.action)
        return step

    def mark_done(self, step_id: int, result: Any = None) -> None:
        """Brain calls this when a step finished successfully."""
        step = self._get_step(step_id)
        if step:
            step.complete(result=result)
            self._check_plan_status()

    def mark_failed(self, step_id: int, error: str, skip_dependents: bool = False) -> None:
        """
        Brain calls this when a step failed.
        If skip_dependents=True — dependent steps are skipped instead of blocking.
        """
        step = self._get_step(step_id)
        if not step:
            return

        step.fail(error=error)

        if skip_dependents and self._active_plan:
            self._skip_dependents(step_id)

        self._check_plan_status()

    def mark_skipped(self, step_id: int) -> None:
        step = self._get_step(step_id)
        if step:
            step.skip()

    # ------------------------------------------------------------------
    # Status & introspection
    # ------------------------------------------------------------------

    def has_active_plan(self) -> bool:
        return self._active_plan is not None

    def status(self) -> dict:
        if not self._active_plan:
            return {"active_plan": None, "history_count": len(self._history)}
        return {
            "active_plan": self._active_plan.to_dict(),
            "history_count": len(self._history),
        }

    def progress(self) -> dict | None:
        if not self._active_plan:
            return None
        return self._active_plan.progress()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_step(self, step_id: int) -> Step | None:
        if not self._active_plan:
            logger.warning("[Planner] mark_done called but no active plan")
            return None
        for step in self._active_plan.steps:
            if step.id == step_id:
                return step
        logger.warning("[Planner] Step id=%d not found", step_id)
        return None

    def _skip_dependents(self, failed_step_id: int) -> None:
        """Skip all steps that depend on the failed step."""
        if not self._active_plan:
            return
        for step in self._active_plan.steps:
            if failed_step_id in step.depends_on and step.status == StepStatus.PENDING:
                step.skip()
                logger.info("[Planner] Skipped dependent step id=%d", step.id)
                # Recursively skip their dependents too
                self._skip_dependents(step.id)

    def _check_plan_status(self) -> None:
        """After each step update — check if plan finished."""
        if not self._active_plan:
            return
        plan = self._active_plan
        if plan.is_complete():
            plan.status = PlanStatus.COMPLETE
            self._history.append(plan)
            self._active_plan = None
            logger.info("[Planner] Plan auto-completed | goal='%s'", plan.goal)
        elif plan.has_blocking_failure():
            plan.status = PlanStatus.FAILED
            self._history.append(plan)
            self._active_plan = None
            logger.error("[Planner] Plan auto-failed | goal='%s'", plan.goal)


# ════════════════════════════════════════════════════════════════════
# PlanCheckpointStore — durable plan state
# ════════════════════════════════════════════════════════════════════

class PlanCheckpointStore:
    """SQLite-backed checkpoint for in-flight Plans.

    The WorkflowRunner calls `save(job_id, plan)` after each step
    transitions. On process restart, `load(job_id)` returns the last
    saved plan (or None) so the runner can resume from the next
    pending step.

    Schema is dead simple: one row per `(job_id, plan_seq)`. We keep
    the full history of saves for the same job so post-mortems can
    reconstruct the sequence; `latest(job_id)` returns the freshest.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_checkpoints (
                seq      INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id   TEXT NOT NULL,
                ts       TEXT NOT NULL,
                plan_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plan_job ON plan_checkpoints(job_id)"
        )
        self._conn.commit()

    def save(self, job_id: str, plan: Plan) -> int:
        """Persist a snapshot of `plan` for `job_id`. Returns checkpoint seq."""
        ts = datetime.utcnow().isoformat()
        blob = json.dumps(plan.to_dict(), ensure_ascii=False, default=str)
        cur = self._conn.execute(
            "INSERT INTO plan_checkpoints (job_id, ts, plan_json) VALUES (?, ?, ?)",
            (job_id, ts, blob),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def latest(self, job_id: str) -> Plan | None:
        row = self._conn.execute(
            "SELECT plan_json FROM plan_checkpoints WHERE job_id = ? "
            "ORDER BY seq DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Plan.from_dict(json.loads(row["plan_json"]))

    def history(self, job_id: str) -> list[Plan]:
        rows = self._conn.execute(
            "SELECT plan_json FROM plan_checkpoints WHERE job_id = ? "
            "ORDER BY seq ASC",
            (job_id,),
        ).fetchall()
        return [Plan.from_dict(json.loads(r["plan_json"])) for r in rows]

    def unfinished_jobs(self) -> list[str]:
        """List job_ids whose most-recent checkpoint isn't terminal."""
        rows = self._conn.execute(
            """
            SELECT job_id, plan_json FROM plan_checkpoints
            WHERE seq IN (
                SELECT MAX(seq) FROM plan_checkpoints GROUP BY job_id
            )
            """
        ).fetchall()
        out = []
        for r in rows:
            try:
                data = json.loads(r["plan_json"])
                if data.get("status") in (PlanStatus.ACTIVE.value, "active"):
                    out.append(r["job_id"])
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    def clear(self, job_id: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM plan_checkpoints WHERE job_id = ?", (job_id,),
        )
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PlanCheckpointStore":
        return self

    def __exit__(self, *_a) -> None:
        self.close()
