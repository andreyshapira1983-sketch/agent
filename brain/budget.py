"""
brain/budget.py — BudgetController.

A single source of truth for "are we still within bounds?" decisions:

    - per-session token / dollar / wall-clock budgets
    - per-job token / dollar / wall-clock budgets
    - per-day rolling caps

The controller is **read-mostly**: callers ask `check(cost)` before
performing an operation, then `record(cost)` once it actually completed.
This keeps the agent honest: a dry-run costs zero, a failed call only
charges what it actually consumed.

Integration points
──────────────────
- LLM adapter calls `check(tokens, dollars)` before sending the request,
  records on success.
- WorkflowRunner calls `check(seconds=...)` at each step's start.
- PolicyEngine consults `is_exhausted()` via a custom rule
  (`budget_exhausted → DENY`).

Thread-safety
─────────────
All state mutations go through an `RLock`. The controller is in-memory
only; persistence is the AuditLog's job (each `record()` may be paired
with an audit entry by the caller).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, date, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Public types
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BudgetLimits:
    """One scope's caps. `None` means "no limit on this dimension"."""

    tokens:  int | None = None
    dollars: float | None = None
    seconds: float | None = None

    def is_unlimited(self) -> bool:
        return self.tokens is None and self.dollars is None and self.seconds is None


@dataclass
class BudgetUsage:
    """Running totals for one scope."""

    tokens:  int = 0
    dollars: float = 0.0
    seconds: float = 0.0

    def add(self, *, tokens: int = 0, dollars: float = 0.0, seconds: float = 0.0) -> None:
        self.tokens  += int(tokens)
        self.dollars += float(dollars)
        self.seconds += float(seconds)

    def to_dict(self) -> dict:
        return {"tokens": self.tokens, "dollars": round(self.dollars, 4), "seconds": round(self.seconds, 3)}


class BudgetExceeded(RuntimeError):
    """Raised by `BudgetController.charge()` when a cost would breach a limit."""

    def __init__(self, scope: str, dimension: str, used: float, limit: float):
        super().__init__(
            f"budget exceeded in scope={scope!r}: {dimension}={used} would breach limit={limit}"
        )
        self.scope = scope
        self.dimension = dimension
        self.used = used
        self.limit = limit


# ════════════════════════════════════════════════════════════════════
# Controller
# ════════════════════════════════════════════════════════════════════

class BudgetController:
    """In-memory budget tracker scoped by session / job / day.

    Usage:

        ctrl = BudgetController(
            per_job=BudgetLimits(tokens=20_000, dollars=0.20, seconds=120),
            per_day=BudgetLimits(dollars=2.00),
        )

        with ctrl.scope_job("j1"):
            ok, reason = ctrl.check(tokens=1000, dollars=0.01)
            if not ok:
                raise SystemExit(reason)
            # ... run the LLM call ...
            ctrl.record(tokens=987, dollars=0.009)

    The `with` block doesn't transact rollback — exiting the block just
    pops the job scope. Charges already recorded stay recorded.
    """

    def __init__(
        self,
        *,
        per_session: BudgetLimits = BudgetLimits(),
        per_job:     BudgetLimits = BudgetLimits(),
        per_day:     BudgetLimits = BudgetLimits(),
    ) -> None:
        self._per_session_limit = per_session
        self._per_job_limit = per_job
        self._per_day_limit = per_day
        self._lock = threading.RLock()
        self._session_usage = BudgetUsage()
        self._job_usage: dict[str, BudgetUsage] = {}
        self._daily_usage: dict[date, BudgetUsage] = {}
        self._current_job: str | None = None
        self._created_at = time.monotonic()

    # ────────────────────────────────────────────────────────────────
    # Scope management
    # ────────────────────────────────────────────────────────────────

    def scope_job(self, job_id: str) -> "BudgetJobScope":
        """Context manager that activates per-job tracking."""
        return BudgetJobScope(self, job_id)

    # ────────────────────────────────────────────────────────────────
    # Check + record
    # ────────────────────────────────────────────────────────────────

    def check(
        self,
        *,
        tokens: int = 0,
        dollars: float = 0.0,
        seconds: float = 0.0,
    ) -> tuple[bool, str]:
        """Would charging this cost exceed any active limit?

        Returns (ok, reason). When `ok=False`, `reason` names the breached
        scope and dimension; caller decides whether to abort or downgrade.
        """
        with self._lock:
            for scope_name, current, limit in self._iter_scopes():
                if limit.tokens is not None and current.tokens + tokens > limit.tokens:
                    return (False, f"{scope_name}: tokens {current.tokens + tokens} > {limit.tokens}")
                if limit.dollars is not None and current.dollars + dollars > limit.dollars:
                    return (False, f"{scope_name}: dollars {current.dollars + dollars:.4f} > {limit.dollars}")
                if limit.seconds is not None and current.seconds + seconds > limit.seconds:
                    return (False, f"{scope_name}: seconds {current.seconds + seconds:.2f} > {limit.seconds}")
        return (True, "")

    def record(
        self,
        *,
        tokens: int = 0,
        dollars: float = 0.0,
        seconds: float = 0.0,
    ) -> None:
        """Charge this cost against all active scopes."""
        with self._lock:
            self._session_usage.add(tokens=tokens, dollars=dollars, seconds=seconds)
            if self._current_job is not None:
                self._job_usage.setdefault(self._current_job, BudgetUsage()).add(
                    tokens=tokens, dollars=dollars, seconds=seconds,
                )
            today = datetime.now(timezone.utc).date()
            self._daily_usage.setdefault(today, BudgetUsage()).add(
                tokens=tokens, dollars=dollars, seconds=seconds,
            )

    def charge(
        self,
        *,
        tokens: int = 0,
        dollars: float = 0.0,
        seconds: float = 0.0,
    ) -> None:
        """Atomic check + record. Raises BudgetExceeded on breach."""
        ok, reason = self.check(tokens=tokens, dollars=dollars, seconds=seconds)
        if not ok:
            scope, _, dim_msg = reason.partition(": ")
            dim, _, _ = dim_msg.partition(" ")
            raise BudgetExceeded(
                scope=scope, dimension=dim,
                used=tokens or dollars or seconds, limit=0,
            )
        self.record(tokens=tokens, dollars=dollars, seconds=seconds)

    # ────────────────────────────────────────────────────────────────
    # Introspection
    # ────────────────────────────────────────────────────────────────

    def is_exhausted(self) -> bool:
        """Cheap "any scope already breached?" check (no cost added)."""
        ok, _ = self.check()
        return not ok

    def session_usage(self) -> BudgetUsage:
        return replace(self._session_usage)

    def job_usage(self, job_id: str) -> BudgetUsage | None:
        usage = self._job_usage.get(job_id)
        return replace(usage) if usage else None

    def today_usage(self) -> BudgetUsage:
        today = datetime.now(timezone.utc).date()
        return replace(self._daily_usage.get(today, BudgetUsage()))

    def state(self) -> dict:
        """Snapshot for /status endpoints."""
        return {
            "session":  self._session_usage.to_dict(),
            "today":    self.today_usage().to_dict(),
            "current_job": self._current_job,
            "current_job_usage": (
                self._job_usage[self._current_job].to_dict()
                if self._current_job and self._current_job in self._job_usage
                else None
            ),
            "limits": {
                "per_session": _limits_dict(self._per_session_limit),
                "per_job":     _limits_dict(self._per_job_limit),
                "per_day":     _limits_dict(self._per_day_limit),
            },
            "exhausted": self.is_exhausted(),
        }

    # ────────────────────────────────────────────────────────────────
    # Private
    # ────────────────────────────────────────────────────────────────

    def _iter_scopes(self):
        yield ("session", self._session_usage, self._per_session_limit)
        if self._current_job is not None:
            yield ("job", self._job_usage.setdefault(self._current_job, BudgetUsage()), self._per_job_limit)
        today = datetime.now(timezone.utc).date()
        yield ("day", self._daily_usage.setdefault(today, BudgetUsage()), self._per_day_limit)


class BudgetJobScope:
    """Context manager that swaps the controller's `_current_job`."""

    def __init__(self, controller: BudgetController, job_id: str) -> None:
        self._controller = controller
        self._job_id = job_id
        self._previous: Optional[str] = None

    def __enter__(self) -> "BudgetJobScope":
        with self._controller._lock:  # noqa: SLF001
            self._previous = self._controller._current_job  # noqa: SLF001
            self._controller._current_job = self._job_id    # noqa: SLF001
        return self

    def __exit__(self, *_a) -> None:
        with self._controller._lock:  # noqa: SLF001
            self._controller._current_job = self._previous  # noqa: SLF001


def _limits_dict(limits: BudgetLimits) -> dict:
    return {
        "tokens":  limits.tokens,
        "dollars": limits.dollars,
        "seconds": limits.seconds,
    }
