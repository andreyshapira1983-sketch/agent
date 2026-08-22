"""Model usage ledger and budget checks.

The model router decides which model to use. This module records what actually
happened: role, provider, model, token usage and rough cost units. Real money
pricing changes frequently, so the default estimator is deliberately coarse and
safe for budgeting decisions rather than billing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.budget_ledger import BudgetLedger
from core.run_context import current_run, run_cost_ceiling
from core.state_integrity import append_state_jsonl, read_state_jsonl

#: Error-text fragments meaning "this key cannot pay for / is not allowed this
#: request". Owned HERE beside the ledger that stores the error text; the
#: router's `_is_switch_key_error` consumes the same tuple, so the two rules
#: cannot drift apart. Only this class demotes provider health — a flaky
#: network must never take a provider out of rotation.
_KEY_CLASS_TEXT_MARKERS: tuple[str, ...] = (
    "quota",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission denied",
    "billing",
    "payment required",
    "credit balance",
    "insufficient funds",
)

#: Consecutive key-class failures after which a provider is skipped.
_UNHEALTHY_AFTER = 3
#: How long the skip lasts after the newest failure. A cooldown, never a ban:
#: past this the provider gets one probe call again — the operator may have
#: topped the balance up, and the agent may not retire its own toolkit.
_UNHEALTHY_COOLDOWN_MINUTES = 120
#: Bounded tail read for health (MIR-125: this file only grows).
_TAIL_READ_BYTES = 131072

_COST_UNITS_PER_1K_TOKENS = {
    "free": 0,
    "low": 1,
    "medium": 3,
    "high": 8,
    "unknown": 5,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return max(0, int(value.strip()))
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc


def _estimate_tokens(*parts: str) -> int:
    # Cheap deterministic fallback used only when a provider/FakeLLM does not
    # expose token usage. It is intentionally rough, not billing-grade.
    return sum(len(part or "") for part in parts) // 4


@dataclass(frozen=True)
class ModelUsageLimits:
    """Session-level LLM budget limits.

    A zero limit means "not enforced" to preserve current behaviour until the
    operator explicitly sets a cap.
    """

    max_calls: int = 0
    max_tokens: int = 0
    max_cost_units: int = 0

    @classmethod
    def from_env(cls) -> ModelUsageLimits:
        return cls(
            max_calls=_env_int("AGENT_MODEL_MAX_CALLS_PER_SESSION"),
            max_tokens=_env_int("AGENT_MODEL_MAX_TOKENS_PER_SESSION"),
            max_cost_units=_env_int("AGENT_MODEL_MAX_COST_UNITS_PER_SESSION"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "max_cost_units": self.max_cost_units,
            "max_calls_label": _limit_label(self.max_calls),
            "max_tokens_label": _limit_label(self.max_tokens),
            "max_cost_units_label": _limit_label(self.max_cost_units),
            "max_calls_enforced": self.max_calls > 0,
            "max_tokens_enforced": self.max_tokens > 0,
            "max_cost_units_enforced": self.max_cost_units > 0,
        }


@dataclass(frozen=True)
class ModelUsageRecord:
    role: str
    provider: str
    model: str
    route_reason: str
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_tier: str
    cost_units: int
    estimated: bool
    started_at: str
    completed_at: str
    duration_ms: int
    error: str | None = None
    # Which run spent this. Optional and last so a ledger written before this
    # field existed still constructs — load_records() drops rows it cannot
    # build and does so silently, so a required field here would erase spend
    # history rather than report a problem.
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "route_reason": self.route_reason,
            "status": self.status,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_tier": self.cost_tier,
            "cost_units": self.cost_units,
            "estimated": self.estimated,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "run_id": self.run_id,
        }


class ModelBudgetExceeded(RuntimeError):
    """Raised when a configured budget blocks an LLM call."""

    def __init__(
        self,
        message: str,
        *,
        counter: str = "",
        role: str = "",
        provider: str = "",
        model: str = "",
        used: int | None = None,
        limit: int | None = None,
        window: str | None = None,
    ) -> None:
        super().__init__(message)
        self.counter = counter
        self.role = role
        self.provider = provider
        self.model = model
        self.used = used
        self.limit = limit
        self.window = window

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "counter": self.counter,
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "used": self.used,
            "limit": self.limit,
            "window": self.window,
        }


@dataclass
class ModelUsageLedger:
    path: Path | None = None
    limits: ModelUsageLimits = field(default_factory=ModelUsageLimits)
    logger: Any | None = None
    budget_ledger: BudgetLedger | None = None
    records: list[ModelUsageRecord] = field(default_factory=list)
    # Fallback identity for records written outside any run, and the value an
    # explicit caller pins. The ledger file is append-only and outlives the
    # process, so without an id every run's spend lands in one undifferentiated
    # history. Left unset it takes `TraceLogger.trace_id`, which names the
    # AGENT SESSION — a sub-agent carries its own trace id and so stays
    # separable from its parent for free. Which run a given record belongs to
    # is decided per record in `_run_id_for_record`, because one session runs
    # many cycles.
    run_id: str | None = None
    # Set when the caller named the run itself. A surrounding run scope must
    # not override that: a sub-agent ledger is built for one run and stays
    # pinned to it.
    _run_id_pinned: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # bootstrap already holds the agent's TraceLogger when it builds the
        # ledger, so taking the id from there spares every call site from
        # passing the same value twice. An explicit run_id still wins.
        self._run_id_pinned = self.run_id is not None
        if self.run_id is None:
            self.run_id = getattr(self.logger, "trace_id", None)

    def _run_id_for_record(self) -> str | None:
        """Which run to bill this call to."""
        if not self._run_id_pinned:
            active = current_run()
            if active is not None and active.run_id:
                return active.run_id
        return self.run_id

    @classmethod
    def from_env(
        cls,
        *,
        path: Path | None = None,
        logger: Any | None = None,
        budget_ledger: BudgetLedger | None = None,
    ) -> ModelUsageLedger:
        return cls(
            path=path,
            limits=ModelUsageLimits.from_env(),
            logger=logger,
            budget_ledger=budget_ledger,
        )

    def assert_can_start(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        system: str = "",
        user: str = "",
        max_output_tokens: int = 0,
        cost_tier: str = "unknown",
    ) -> None:
        """Pre-flight budget gate, run *before* an LLM call is dispatched."""
        totals = _totals(self.records)
        est_tokens = _estimate_tokens(system, user) + max(0, int(max_output_tokens))
        est_cost = estimate_cost_units(est_tokens, cost_tier)

        if self.limits.max_calls and totals["calls"] >= self.limits.max_calls:
            raise ModelBudgetExceeded(
                f"model call budget exhausted: {totals['calls']}/"
                f"{self.limits.max_calls} before {role}:{provider}/{model}",
                counter="llm_calls",
                role=role,
                provider=provider,
                model=model,
                used=totals["calls"],
                limit=self.limits.max_calls,
            )
        if (
            self.limits.max_tokens
            and totals["total_tokens"] >= self.limits.max_tokens
        ):
            raise ModelBudgetExceeded(
                f"model token budget exhausted: {totals['total_tokens']}/"
                f"{self.limits.max_tokens} before {role}:{provider}/{model}",
                counter="model_tokens",
                role=role,
                provider=provider,
                model=model,
                used=totals["total_tokens"],
                limit=self.limits.max_tokens,
            )
        if (
            self.limits.max_tokens
            and est_tokens > 0
            and totals["total_tokens"] + est_tokens > self.limits.max_tokens
        ):
            raise ModelBudgetExceeded(
                f"model token budget would exhaust: {totals['total_tokens']}+~"
                f"{est_tokens}/{self.limits.max_tokens} before "
                f"{role}:{provider}/{model}"
            )
        if (
            self.limits.max_cost_units
            and totals["cost_units"] >= self.limits.max_cost_units
        ):
            raise ModelBudgetExceeded(
                f"model cost budget exhausted: {totals['cost_units']}/"
                f"{self.limits.max_cost_units} before {role}:{provider}/{model}",
                counter="model_cost_units",
                role=role,
                provider=provider,
                model=model,
                used=totals["cost_units"],
                limit=self.limits.max_cost_units,
            )
        if (
            self.limits.max_cost_units
            and est_cost > 0
            and totals["cost_units"] + est_cost > self.limits.max_cost_units
        ):
            raise ModelBudgetExceeded(
                f"model cost budget would exhaust: {totals['cost_units']}+~"
                f"{est_cost}/{self.limits.max_cost_units} before "
                f"{role}:{provider}/{model}"
            )
        # Run-scoped cost envelope (MIR-116): a campaign or any caller may bound
        # what the session counter can reach WHILE its run is active, so the cap
        # acts here — before dispatch — instead of between cycles.
        ceiling = run_cost_ceiling()
        if ceiling is not None:
            if totals["cost_units"] >= ceiling:
                raise ModelBudgetExceeded(
                    f"run cost envelope exhausted: {totals['cost_units']}/"
                    f"{ceiling} before {role}:{provider}/{model}",
                    counter="model_cost_units",
                    role=role,
                    provider=provider,
                    model=model,
                    used=totals["cost_units"],
                    limit=ceiling,
                )
            if est_cost > 0 and totals["cost_units"] + est_cost > ceiling:
                raise ModelBudgetExceeded(
                    f"run cost envelope would exhaust: {totals['cost_units']}+~"
                    f"{est_cost}/{ceiling} before {role}:{provider}/{model}"
                )
        if self.budget_ledger is not None:
            # Read-only pre-flight peeks against persistent windows first, so a
            # blocked estimate never records a phantom llm_call.
            if est_tokens > 0:
                token_peek = self.budget_ledger.check(
                    "model_tokens",
                    amount=est_tokens,
                    reason=f"pre-flight model tokens: {role}:{provider}/{model}",
                )
                if not token_peek.allowed:
                    raise ModelBudgetExceeded(
                        f"persistent {token_peek.window} model token budget would "
                        f"exhaust: {token_peek.used}+~{est_tokens}/{token_peek.limit} "
                        f"before {role}:{provider}/{model}"
                    )
            if est_cost > 0:
                cost_peek = self.budget_ledger.check(
                    "model_cost_units",
                    amount=est_cost,
                    reason=f"pre-flight model cost: {role}:{provider}/{model}",
                )
                if not cost_peek.allowed:
                    raise ModelBudgetExceeded(
                        f"persistent {cost_peek.window} model cost budget would "
                        f"exhaust: {cost_peek.used}+~{est_cost}/{cost_peek.limit} "
                        f"before {role}:{provider}/{model}"
                    )
            decision = self.budget_ledger.reserve(
                "llm_calls",
                amount=1,
                reason=f"model call: {role}:{provider}/{model}",
                scope="model_usage",
            )
            if not decision.allowed:
                raise ModelBudgetExceeded(
                    f"persistent {decision.window} model call budget exhausted: "
                    f"{decision.used}/{decision.limit} before {role}:{provider}/{model}",
                    counter=decision.counter,
                    role=role,
                    provider=provider,
                    model=model,
                    used=decision.used,
                    limit=decision.limit,
                    window=decision.window,
                )

    def log_start(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        route_reason: str,
        cost_tier: str,
    ) -> None:
        if self.logger is None:
            return
        self.logger.log(
            "model_call_start",
            {
                "role": role,
                "provider": provider,
                "model": model,
                "route_reason": route_reason,
                "cost_tier": cost_tier,
                "session_totals_before": _totals(self.records),
                "limits": self.limits.to_dict(),
            },
        )

    def record(  # noqa: PLR0913 — flat: depth 2, all 1 returns are guard clauses
        self,
        *,
        role: str,
        provider: str,
        model: str,
        route_reason: str,
        cost_tier: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool,
        started_at: str,
        completed_at: str,
        duration_ms: int,
        error: str | None = None,
    ) -> ModelUsageRecord:
        total_tokens = max(0, int(input_tokens)) + max(0, int(output_tokens))
        record = ModelUsageRecord(
            role=role,
            provider=provider,
            model=model,
            route_reason=route_reason,
            status=status,
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            total_tokens=total_tokens,
            cost_tier=cost_tier,
            cost_units=estimate_cost_units(total_tokens, cost_tier),
            estimated=estimated,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int(duration_ms)),
            error=error,
            run_id=self._run_id_for_record(),
        )
        self.records.append(record)
        if self.budget_ledger is not None:
            if record.total_tokens > 0:
                self.budget_ledger.record(
                    "model_tokens",
                    amount=record.total_tokens,
                    reason=f"model tokens: {role}:{provider}/{model}",
                    scope="model_usage",
                )
            if record.cost_units > 0:
                self.budget_ledger.record(
                    "model_cost_units",
                    amount=record.cost_units,
                    reason=f"model cost units: {role}:{provider}/{model}",
                    scope="model_usage",
                )
        if self.path is not None:
            append_state_jsonl(self.path, [record.to_dict()])
        if self.logger is not None:
            self.logger.log("model_call_end", record.to_dict())
        return record

    def provider_unhealthy(
        self, provider: str, *, now: datetime | None = None,
    ) -> str | None:
        """Reason this provider should be skipped right now, or None.

        Derived from the ledger's own recent records — no new state, no new
        writer. MIR-132 measured why: **presence of an API key was treated as
        availability**, and one provider answered "credit balance is too low"
        391 consecutive times across four days while every fresh process began
        with it again. Per-call failover rescued the work; nothing remembered.

        Unhealthy means ALL of: the provider's newest records show
        ``_UNHEALTHY_AFTER`` consecutive key/quota/billing-class failures
        (`_KEY_CLASS_TEXT_MARKERS` — the same conservatism as the router's
        switch rule: a flaky network never demotes); no success since; and the
        newest failure is younger than ``_UNHEALTHY_COOLDOWN_MINUTES`` — past
        that the provider is probed again, because a cooldown is a delay and a
        ban would be the agent retiring part of its own toolkit (MIR-131/§9).
        One success heals everything. No readable history reads as healthy:
        refusing to work is worse than one wasted probe call.
        """
        wanted = (provider or "").strip().lower()
        if not wanted:
            return None
        rows = self._recent_rows_for(wanted)
        if not rows:
            return None
        streak = 0
        newest_failure: str | None = None
        for row in reversed(rows):  # newest first
            status = str(row.get("status") or "")
            if status == "success":
                return None
            error = str(row.get("error") or "").lower()
            if not any(marker in error for marker in _KEY_CLASS_TEXT_MARKERS):
                return None  # a non-key error breaks the chain: stay conservative
            streak += 1
            if newest_failure is None:
                newest_failure = str(row.get("completed_at") or "")
            if streak >= _UNHEALTHY_AFTER:
                break
        if streak < _UNHEALTHY_AFTER or not newest_failure:
            return None
        try:
            newest = datetime.fromisoformat(newest_failure)
        except ValueError:
            return None
        moment = now or datetime.now(timezone.utc)
        age = (moment - newest).total_seconds() / 60.0
        if age > _UNHEALTHY_COOLDOWN_MINUTES:
            return None  # stale: time to probe again
        return f"{streak}_consecutive_key_errors_within_{int(age)}m"

    def _recent_rows_for_any(self, *, limit: int = 200) -> list[dict]:
        """Newest raw rows regardless of provider — used by the operator's
        status line to learn WHICH providers this workspace even uses, so the
        health report names the real set instead of a hardcoded list."""
        return self._recent_rows(limit=limit)

    def _recent_rows(self, *, limit: int) -> list[dict]:
        """Bounded tail of raw rows, newest last (MIR-125's lesson at birth)."""
        if self.path is None or not self.path.exists():
            return [r.to_dict() for r in self.records][-limit:]
        try:
            with self.path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - _TAIL_READ_BYTES))
                chunk = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = chunk.splitlines()
        if size > _TAIL_READ_BYTES and lines:
            lines = lines[1:]
        rows: list[dict] = []
        for line in lines:
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            payload = raw.get("payload", raw)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows[-limit:]

    def _recent_rows_for(self, provider: str, *, limit: int = 40) -> list[dict]:
        """Newest-last raw rows for *provider*, from the shared bounded tail.

        The MIR-125 lesson applied at birth rather than retrofitted: this file
        only grows, and health needs the last few records, so only the final
        ``_TAIL_READ_BYTES`` are read and parsed.
        """
        wanted = provider.strip().lower()
        rows = self._recent_rows(limit=10_000)
        out = [r for r in rows
               if str(r.get("provider") or "").strip().lower() == wanted]
        return out[-limit:]

    def load_records(self) -> list[ModelUsageRecord]:
        if self.path is None or not self.path.exists():
            return list(self.records)
        loaded: list[ModelUsageRecord] = []
        for data in read_state_jsonl(self.path):
            try:
                loaded.append(ModelUsageRecord(**data))
            except (TypeError, ValueError):
                continue
        return loaded

    def session_cost_units(self) -> int:
        """Cost units this session's records sum to — the counter the run cost
        envelope bounds, exposed so a caller can compute its ceiling from the
        same source the gate enforces against."""
        return _totals(self.records)["cost_units"]

    def snapshot(self) -> dict[str, Any]:
        records = self.load_records()
        totals = _totals(records)
        session_totals = _totals(self.records)
        by_role: dict[str, dict[str, int]] = {}
        by_model: dict[str, dict[str, int]] = {}
        for record in records:
            role_bucket = by_role.setdefault(
                record.role,
                {"calls": 0, "total_tokens": 0, "cost_units": 0},
            )
            role_bucket["calls"] += 1
            role_bucket["total_tokens"] += record.total_tokens
            role_bucket["cost_units"] += record.cost_units
            model_key = f"{record.provider}/{record.model}"
            model_bucket = by_model.setdefault(
                model_key,
                {"calls": 0, "total_tokens": 0, "cost_units": 0},
            )
            model_bucket["calls"] += 1
            model_bucket["total_tokens"] += record.total_tokens
            model_bucket["cost_units"] += record.cost_units
        return {
            "path": str(self.path) if self.path is not None else None,
            "limits": self.limits.to_dict(),
            "totals": totals,
            "session_totals": session_totals,
            "by_role": by_role,
            "by_model": by_model,
            "recent": [r.to_dict() for r in records[-10:]],
            "persistent_budget_windows": (
                self.budget_ledger.snapshot() if self.budget_ledger is not None else None
            ),
        }


def estimate_cost_units(total_tokens: int, cost_tier: str) -> int:
    if total_tokens <= 0:
        return 0
    units_per_1k = _COST_UNITS_PER_1K_TOKENS.get(
        cost_tier,
        _COST_UNITS_PER_1K_TOKENS["unknown"],
    )
    return ((total_tokens + 999) // 1000) * units_per_1k


def _limit_label(limit: int) -> str:
    return str(limit) if int(limit) > 0 else "unlimited"


def _totals(records: list[ModelUsageRecord]) -> dict[str, int]:
    return {
        "calls": len(records),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "total_tokens": sum(r.total_tokens for r in records),
        "cost_units": sum(r.cost_units for r in records),
    }


def usage_from_llm_or_estimate(
    llm: Any,
    *,
    system: str,
    user: str,
    output: str,
) -> tuple[int, int, bool]:
    last_usage = getattr(llm, "last_usage", None)
    if isinstance(last_usage, dict):
        input_tokens = int(last_usage.get("input_tokens") or 0)
        output_tokens = int(last_usage.get("output_tokens") or 0)
        if input_tokens or output_tokens:
            return input_tokens, output_tokens, False
    return _estimate_tokens(system, user), _estimate_tokens(output), True


def utc_now_iso() -> str:
    return _utc_now_iso()
