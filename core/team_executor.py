"""Executor for bounded subagent contracts.

Supports two modes:
- dry_run=True  (default) — walks the plan and reserves/validates contracts
  without spawning any sub-agents.  Safe for inspection and testing.
- dry_run=False — requires a SubAgentRunner injected via the constructor.
  Runs each contract in plan order, blocking on approval_required items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from core.subagent_contract import CanonicalSubagentContract
from core.subagent_runner import SubagentContractRefused
from core.team_plan import SubagentContract, TeamPlan

if TYPE_CHECKING:
    from core.subagent_runner import SubAgentRunner
    from core.subagent_registry import SubagentRegistry


TeamExecutionStatus = Literal[
    "completed",
    "not_needed",
    "blocked",
    "budget_exhausted",
]

TeamStepStatus = Literal[
    "dry_run_planned",
    "approval_required",
    "budget_blocked",
    "executed",
    "error",
]


@dataclass(frozen=True)
class TeamBudget:
    max_model_calls: int = 10
    max_cost_units: int = 20

    def __post_init__(self) -> None:
        if self.max_model_calls < 0 or self.max_cost_units < 0:
            raise ValueError("team budget values must be non-negative")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_cost_units": self.max_cost_units,
        }


@dataclass(frozen=True)
class TeamExecutionStep:
    order: int
    contract_name: str
    role: str
    status: TeamStepStatus
    summary: str
    model_role: str
    reserved_model_calls: int
    reserved_cost_units: int
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    outputs: tuple[str, ...]
    verifier: str
    approval_required: bool
    stop_conditions: tuple[str, ...]
    answer: str = ""  # non-empty only when status == "executed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "contract_name": self.contract_name,
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "model_role": self.model_role,
            "reserved_model_calls": self.reserved_model_calls,
            "reserved_cost_units": self.reserved_cost_units,
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "outputs": list(self.outputs),
            "verifier": self.verifier,
            "approval_required": self.approval_required,
            "stop_conditions": list(self.stop_conditions),
        }


@dataclass(frozen=True)
class VerifierHandoff:
    verifier: str
    contract_names: tuple[str, ...]
    required_outputs: tuple[str, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier": self.verifier,
            "contract_names": list(self.contract_names),
            "required_outputs": list(self.required_outputs),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class TeamExecutionReport:
    goal: str
    dry_run: bool
    status: TeamExecutionStatus
    budget: TeamBudget
    used_model_calls: int
    used_cost_units: int
    steps: tuple[TeamExecutionStep, ...]
    verifier_handoffs: tuple[VerifierHandoff, ...]
    warnings: tuple[str, ...] = ()
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "dry_run": self.dry_run,
            "status": self.status,
            "budget": self.budget.to_dict(),
            "used_model_calls": self.used_model_calls,
            "used_cost_units": self.used_cost_units,
            "steps": [step.to_dict() for step in self.steps],
            "verifier_handoffs": [
                handoff.to_dict() for handoff in self.verifier_handoffs
            ],
            "warnings": list(self.warnings),
            "stop_reason": self.stop_reason,
        }

    def user_summary(self) -> str:
        if self.dry_run:
            header = [
                "=== team execution dry-run ===",
                "WARNING: planning preview only — no subagents were executed.",
            ]
        else:
            header = ["=== team execution ==="]
        lines = [
            *header,
            f"goal: {self.goal}",
            f"status: {self.status}",
            (
                "budget: "
                f"calls={self.used_model_calls}/{self.budget.max_model_calls} "
                f"cost={self.used_cost_units}/{self.budget.max_cost_units}"
            ),
        ]
        if self.stop_reason:
            lines.append(f"stop: {self.stop_reason}")
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        if self.steps:
            lines.append("steps:")
            for step in self.steps:
                tools = ",".join(step.allowed_tools) or "-"
                lines.append(
                    f"  {step.order}. {step.contract_name}: {step.status} "
                    f"model_role={step.model_role} tools={tools} "
                    f"calls={step.reserved_model_calls} cost={step.reserved_cost_units}"
                )
                lines.append(f"     verifier: {step.verifier}")
                if step.answer:
                    preview = step.answer[:120].replace("\n", " ")
                    lines.append(f"     answer  : {preview}")
        if self.verifier_handoffs:
            lines.append("verifier handoffs:")
            for handoff in self.verifier_handoffs:
                lines.append(
                    f"  - {handoff.verifier}: {', '.join(handoff.contract_names)}"
                )
        return "\n".join(lines)


class TeamExecutor:
    """Executor for bounded subagent contracts.

    Parameters
    ----------
    runner:
        Optional SubAgentRunner.  Required when ``dry_run=False``.
        When None (default), the executor operates in dry-run-only mode.
    registry:
        Optional SubagentRegistry for best-effort canonical outcome recording.
        Registry failures become warnings and never replace execution results.
    """

    def __init__(
        self,
        runner: "SubAgentRunner | None" = None,
        registry: "SubagentRegistry | None" = None,
    ) -> None:
        self._runner = runner
        self._registry = registry

    def run(
        self,
        plan: TeamPlan,
        *,
        dry_run: bool = True,
        budget: TeamBudget | None = None,
    ) -> TeamExecutionReport:
        if not dry_run and self._runner is None:
            raise ValueError(
                "TeamExecutor requires a SubAgentRunner to run in non-dry-run mode. "
                "Pass runner= to TeamExecutor() or use dry_run=True."
            )
        budget = budget or TeamBudget()
        if not plan.needed:
            return TeamExecutionReport(
                goal=plan.goal,
                dry_run=True,
                status="not_needed",
                budget=budget,
                used_model_calls=0,
                used_cost_units=0,
                steps=(),
                verifier_handoffs=(),
                warnings=("team plan says subagents are not needed",),
            )

        warnings = list(plan.warnings)
        steps: list[TeamExecutionStep] = []
        used_calls = 0
        used_cost = 0
        status: TeamExecutionStatus = "completed"
        stop_reason = ""

        for order, contract in enumerate(plan.contracts, start=1):
            if (
                used_calls + contract.max_model_calls > budget.max_model_calls
                or used_cost + contract.max_cost_units > budget.max_cost_units
            ):
                step = _step_from_contract(
                    order,
                    contract,
                    status="budget_blocked",
                    summary="Stopped before this contract: team budget would be exceeded.",
                )
                steps.append(step)
                status = "budget_exhausted"
                stop_reason = f"team budget exhausted before {contract.name}"
                warnings.append(stop_reason)
                break

            used_calls += contract.max_model_calls
            used_cost += contract.max_cost_units

            if contract.approval_required:
                step_status: TeamStepStatus = "approval_required"
                summary = "Execution requires approval — skipped."
                if status == "completed":
                    status = "blocked"
                warnings.append(f"{contract.name} requires approval before execution")
                steps.append(_step_from_contract(order, contract, status=step_status, summary=summary))
                continue

            if dry_run:
                step_status = "dry_run_planned"
                summary = "Dry-run planned; no subagent was executed."
                steps.append(_step_from_contract(order, contract, status=step_status, summary=summary))
            else:
                # Real execution crosses the canonical contract boundary.
                assert self._runner is not None  # guarded above
                canonical: CanonicalSubagentContract | None = None
                try:
                    canonical = CanonicalSubagentContract.from_team_contract(contract)
                    result = self._runner.run_contract(
                        canonical,
                    )
                    result_status = str(getattr(result, "status", "success"))
                    if result_status == "success":
                        step_status = "executed"
                        answer = result.answer
                        summary = f"executed: confidence={result.confidence_score:.2f} quality={result.quality_score}"
                        self._record_contract_run(
                            canonical,
                            "executed",
                            warnings,
                            execution_receipt=getattr(result, "execution_receipt", None),
                            audit_report=getattr(result, "contract_audit", None),
                        )
                    else:
                        step_status = "error"
                        answer = ""
                        error = str(getattr(result, "error", "") or result_status)
                        summary = f"error: subagent returned {result_status}: {error}"
                        if status == "completed":
                            status = "blocked"
                        warnings.append(f"{contract.name} failed: {summary}")
                        self._record_contract_run(
                            canonical,
                            "error",
                            warnings,
                            execution_receipt=getattr(result, "execution_receipt", None),
                            audit_report=getattr(result, "contract_audit", None),
                        )
                except Exception as exc:
                    step_status = "error"
                    answer = ""
                    summary = f"error: {type(exc).__name__}: {exc}"
                    if status == "completed":
                        status = "blocked"
                    warnings.append(f"{contract.name} failed: {summary}")
                    if canonical is not None:
                        outcome = (
                            "refused"
                            if isinstance(exc, SubagentContractRefused)
                            else "error"
                        )
                        self._record_contract_run(canonical, outcome, warnings)
                steps.append(
                    _step_from_contract(order, contract, status=step_status, summary=summary, answer=answer if step_status == "executed" else "")
                )

        return TeamExecutionReport(
            goal=plan.goal,
            dry_run=dry_run,
            status=status,
            budget=budget,
            used_model_calls=used_calls,
            used_cost_units=used_cost,
            steps=tuple(steps),
            verifier_handoffs=_verifier_handoffs(tuple(steps)),
            warnings=tuple(_dedupe(warnings)),
            stop_reason=stop_reason,
        )

    def _record_contract_run(
        self,
        contract: CanonicalSubagentContract,
        outcome: str,
        warnings: list[str],
        *,
        execution_receipt: Any | None = None,
        audit_report: Any | None = None,
    ) -> None:
        """Best-effort registry hook; observability must not break execution."""
        if self._registry is None:
            return
        try:
            if audit_report is None and execution_receipt is None:
                self._registry.record_contract_run(contract, outcome)
            else:
                self._registry.record_contract_run(
                    contract,
                    outcome,
                    execution_receipt=execution_receipt,
                    audit_report=audit_report,
                )
        except Exception as exc:
            warnings.append(
                "contract registry write failed: "
                f"{type(exc).__name__}: {exc}"
            )


def _step_from_contract(
    order: int,
    contract: SubagentContract,
    *,
    status: TeamStepStatus,
    summary: str,
    answer: str = "",
) -> TeamExecutionStep:
    return TeamExecutionStep(
        order=order,
        contract_name=contract.name,
        role=contract.role,
        status=status,
        summary=summary,
        model_role=contract.model_role,
        reserved_model_calls=(
            0 if status == "budget_blocked" else contract.max_model_calls
        ),
        reserved_cost_units=(
            0 if status == "budget_blocked" else contract.max_cost_units
        ),
        allowed_tools=contract.allowed_tools,
        forbidden_tools=contract.forbidden_tools,
        outputs=contract.outputs,
        verifier=contract.verifier,
        approval_required=contract.approval_required,
        stop_conditions=contract.stop_conditions,
        answer=answer,
    )


def _verifier_handoffs(steps: tuple[TeamExecutionStep, ...]) -> tuple[VerifierHandoff, ...]:
    buckets: dict[str, list[TeamExecutionStep]] = {}
    for step in steps:
        if step.status == "budget_blocked":
            continue
        buckets.setdefault(step.verifier, []).append(step)
    handoffs: list[VerifierHandoff] = []
    for verifier, bucket in buckets.items():
        required_outputs: list[str] = []
        for step in bucket:
            required_outputs.extend(step.outputs)
        handoffs.append(
            VerifierHandoff(
                verifier=verifier,
                contract_names=tuple(step.contract_name for step in bucket),
                required_outputs=tuple(_dedupe(required_outputs)),
                summary=(
                    f"{verifier} must verify {len(bucket)} planned contract(s) "
                    "before any real delegation is allowed."
                ),
            )
        )
    return tuple(handoffs)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
