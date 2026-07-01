"""Model usage ledger and budget checks.

The model router decides which model to use. This module records what actually
happened: role, provider, model, token usage and rough cost units. Real money
pricing changes frequently, so the default estimator is deliberately coarse and
safe for budgeting decisions rather than billing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.budget_ledger import BudgetLedger
from core.state_integrity import append_state_jsonl, read_state_jsonl


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
    def from_env(cls) -> "ModelUsageLimits":
        return cls(
            max_calls=_env_int("AGENT_MODEL_MAX_CALLS_PER_SESSION"),
            max_tokens=_env_int("AGENT_MODEL_MAX_TOKENS_PER_SESSION"),
            max_cost_units=_env_int("AGENT_MODEL_MAX_COST_UNITS_PER_SESSION"),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "max_cost_units": self.max_cost_units,
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

    @classmethod
    def from_env(
        cls,
        *,
        path: Path | None = None,
        logger: Any | None = None,
        budget_ledger: BudgetLedger | None = None,
    ) -> "ModelUsageLedger":
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
        """Pre-flight budget gate, run *before* an LLM call is dispatched.

        In addition to the reactive checks against already-spent session totals,
        this estimates the cost of the *upcoming* call from the prompt size plus
        the requested output cap and blocks when ``totals + estimate`` would
        exceed a configured session limit or a persistent budget window. This
        prevents a single oversized call from blowing past the cap before any
        tokens have been recorded. The estimate parameters are optional so
        existing callers keep their previous behaviour.
        """
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

    def record(
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
