"""Pure post-run audit policy for canonical subagent contracts.

The policy deliberately consumes an explicit execution receipt instead of
inspecting mutable runtime objects.  Missing observations are reported as
``unknown`` and can never produce a false passing verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.subagent_contract import CanonicalSubagentContract


AuditVerdict = Literal["pass", "fail", "unknown"]
AuditIssueKind = Literal["violation", "unknown"]
VerifierStatus = Literal["passed", "failed", "not_run"]


@dataclass(frozen=True)
class SubagentExecutionReceipt:
    """Measured facts from one subagent execution.

    ``None`` means that the runtime did not measure the fact.  It never means
    zero, false, or empty.
    """

    contract_id: str
    schema_version: int
    approval_granted: bool | None = None
    used_tools: tuple[str, ...] | None = None
    memory_read_tags: tuple[str, ...] | None = None
    memory_write_tags: tuple[str, ...] | None = None
    model_calls: int | None = None
    iterations: int | None = None
    cost_units: int | None = None
    web_fetches: int | None = None
    file_writes: int | None = None
    verifier_status: VerifierStatus | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("contract_id must be non-empty")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        for name in (
            "model_calls",
            "iterations",
            "cost_units",
            "web_fetches",
            "file_writes",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.verifier_status not in {None, "passed", "failed", "not_run"}:
            raise ValueError("verifier_status is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "approval_granted": self.approval_granted,
            "used_tools": list(self.used_tools) if self.used_tools is not None else None,
            "memory_read_tags": (
                list(self.memory_read_tags)
                if self.memory_read_tags is not None
                else None
            ),
            "memory_write_tags": (
                list(self.memory_write_tags)
                if self.memory_write_tags is not None
                else None
            ),
            "model_calls": self.model_calls,
            "iterations": self.iterations,
            "cost_units": self.cost_units,
            "web_fetches": self.web_fetches,
            "file_writes": self.file_writes,
            "verifier_status": self.verifier_status,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True)
class ContractAuditIssue:
    code: str
    kind: AuditIssueKind
    message: str
    declared: Any = None
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "message": self.message,
            "declared": self.declared,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class ContractAuditReport:
    contract_id: str
    schema_version: int
    verdict: AuditVerdict
    issues: tuple[ContractAuditIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "verdict": self.verdict,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def audit_subagent_execution(
    contract: CanonicalSubagentContract,
    receipt: SubagentExecutionReceipt,
) -> ContractAuditReport:
    """Compare measured execution facts with declared contract policy."""
    issues: list[ContractAuditIssue] = []

    def violation(code: str, message: str, declared: Any, observed: Any) -> None:
        issues.append(ContractAuditIssue(code, "violation", message, declared, observed))

    def unknown(code: str, message: str, declared: Any = None) -> None:
        issues.append(ContractAuditIssue(code, "unknown", message, declared, None))

    if receipt.contract_id != contract.contract_id:
        violation(
            "contract_id_mismatch",
            "execution receipt belongs to a different contract",
            contract.contract_id,
            receipt.contract_id,
        )
    if receipt.schema_version != contract.schema_version:
        violation(
            "schema_version_mismatch",
            "execution receipt uses a different contract schema version",
            contract.schema_version,
            receipt.schema_version,
        )

    if contract.approval_required:
        if receipt.approval_granted is None:
            unknown("approval_unmeasured", "approval decision was not measured", True)
        elif not receipt.approval_granted:
            violation("approval_missing", "required approval was not granted", True, False)

    if receipt.used_tools is None:
        unknown("tools_unmeasured", "used tools were not measured")
    else:
        allowed = set(contract.tool_scope.allowed_tools)
        forbidden = set(contract.tool_scope.forbidden_tools)
        for tool in sorted(set(receipt.used_tools)):
            if tool in forbidden:
                violation(
                    "forbidden_tool_used",
                    "execution used an explicitly forbidden tool",
                    sorted(forbidden),
                    tool,
                )
            elif tool not in allowed:
                violation(
                    "tool_not_allowed",
                    "execution used a tool outside the allow-list",
                    sorted(allowed),
                    tool,
                )

    memory = contract.memory_scope
    if memory is None:
        unknown(
            "memory_scope_undeclared",
            "the source contract did not declare persistent-memory policy",
        )
    else:
        _audit_tag_scope(
            issues,
            code_prefix="memory_read",
            observed=receipt.memory_read_tags,
            allowed=memory.read_tags,
        )
        _audit_tag_scope(
            issues,
            code_prefix="memory_write",
            observed=receipt.memory_write_tags,
            allowed=memory.write_tags,
        )

    _audit_limit(issues, "model_calls", receipt.model_calls, contract.budget_scope.max_model_calls)
    _audit_limit(issues, "iterations", receipt.iterations, contract.budget_scope.max_iterations)
    _audit_optional_limit(
        issues, "cost_units", receipt.cost_units, contract.budget_scope.max_cost_units
    )
    _audit_optional_limit(
        issues, "web_fetches", receipt.web_fetches, contract.budget_scope.max_web_fetches
    )
    _audit_optional_limit(
        issues, "file_writes", receipt.file_writes, contract.budget_scope.max_file_writes
    )

    if contract.tool_scope.read_only:
        if receipt.file_writes is None:
            unknown("read_only_unmeasured", "file writes were not measured", 0)
        elif receipt.file_writes > 0:
            violation(
                "read_only_violated",
                "read-only execution performed file writes",
                0,
                receipt.file_writes,
            )

    if contract.verifier is not None:
        if receipt.verifier_status is None:
            unknown("verifier_unmeasured", "verifier outcome was not measured", contract.verifier)
        elif receipt.verifier_status != "passed":
            violation(
                "verifier_not_passed",
                "declared verifier did not pass",
                "passed",
                receipt.verifier_status,
            )

    if contract.stop_conditions:
        if receipt.stop_reason is None:
            unknown(
                "stop_reason_unmeasured",
                "stop reason was not measured",
                list(contract.stop_conditions),
            )
        elif receipt.stop_reason not in contract.stop_conditions:
            violation(
                "stop_condition_not_declared",
                "execution stopped for a reason outside the declared conditions",
                list(contract.stop_conditions),
                receipt.stop_reason,
            )

    verdict: AuditVerdict
    if any(issue.kind == "violation" for issue in issues):
        verdict = "fail"
    elif issues:
        verdict = "unknown"
    else:
        verdict = "pass"
    return ContractAuditReport(
        contract_id=contract.contract_id,
        schema_version=contract.schema_version,
        verdict=verdict,
        issues=tuple(issues),
    )


def _audit_tag_scope(
    issues: list[ContractAuditIssue],
    *,
    code_prefix: str,
    observed: tuple[str, ...] | None,
    allowed: tuple[str, ...],
) -> None:
    if observed is None:
        issues.append(
            ContractAuditIssue(
                f"{code_prefix}_unmeasured",
                "unknown",
                f"{code_prefix.replace('_', ' ')} tags were not measured",
                list(allowed),
                None,
            )
        )
        return
    outside = sorted(set(observed) - set(allowed))
    if outside:
        issues.append(
            ContractAuditIssue(
                f"{code_prefix}_outside_scope",
                "violation",
                f"{code_prefix.replace('_', ' ')} used tags outside the declared scope",
                list(allowed),
                outside,
            )
        )


def _audit_limit(
    issues: list[ContractAuditIssue],
    name: str,
    observed: int | None,
    limit: int,
) -> None:
    if observed is None:
        issues.append(
            ContractAuditIssue(
                f"{name}_unmeasured",
                "unknown",
                f"{name.replace('_', ' ')} were not measured",
                limit,
                None,
            )
        )
    elif observed > limit:
        issues.append(
            ContractAuditIssue(
                f"{name}_exceeded",
                "violation",
                f"{name.replace('_', ' ')} exceeded the declared limit",
                limit,
                observed,
            )
        )


def _audit_optional_limit(
    issues: list[ContractAuditIssue],
    name: str,
    observed: int | None,
    limit: int | None,
) -> None:
    if limit is not None:
        _audit_limit(issues, name, observed, limit)
