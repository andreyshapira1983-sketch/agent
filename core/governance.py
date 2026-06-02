"""Governance modes for safe autonomous growth.

This module separates *what the agent is trying to do* from the lower-level
tool risk policy. PolicyGate answers "is this tool call safe?". Governance
answers "is this kind of autonomous behaviour allowed in this mode?".

The five modes mirror the architecture notes:

  - diagnostic   -> inspect logs/tests/evidence
  - learning     -> ingest and store useful knowledge
  - repair       -> diagnose + propose/apply a fix under approval
  - improvement  -> add or improve capabilities under stricter approval
  - governance   -> change policy/permissions/rollback boundaries

Rules are conservative by default. Anything that writes code, changes
policies, runs shell, or performs external side effects escalates to human
approval. Repair writes are allowed only after a verified diagnosis and with
a rollback path; tests are then run after the write, and rollback may be
applied automatically when verification fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class AgentMode(str, Enum):
    DIAGNOSTIC = "diagnostic"
    LEARNING = "learning"
    REPAIR = "repair"
    IMPROVEMENT = "improvement"
    GOVERNANCE = "governance"


class GovernedOperation(str, Enum):
    READ_LOGS = "read_logs"
    RUN_TESTS = "run_tests"
    READ_SOURCE = "read_source"
    FETCH_WEB = "fetch_web"
    WRITE_MEMORY = "write_memory"
    PROPOSE_DIFF = "propose_diff"
    APPLY_CODE_CHANGE = "apply_code_change"
    RUN_SHELL = "run_shell"
    ROLLBACK = "rollback"
    ADD_TOOL = "add_tool"
    CHANGE_POLICY = "change_policy"
    ENABLE_EXTERNAL_CHANNEL = "enable_external_channel"


GovernanceVerdict = Literal["allow", "require_approval", "deny"]


@dataclass(frozen=True)
class GovernanceDecision:
    mode: AgentMode
    operation: GovernedOperation
    verdict: GovernanceVerdict
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    @property
    def requires_approval(self) -> bool:
        return self.verdict == "require_approval"

    @property
    def denied(self) -> bool:
        return self.verdict == "deny"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "operation": self.operation.value,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


_READ_ONLY = frozenset({
    GovernedOperation.READ_LOGS,
    GovernedOperation.RUN_TESTS,
    GovernedOperation.READ_SOURCE,
    GovernedOperation.FETCH_WEB,
})

_MEMORY_WRITES = frozenset({
    GovernedOperation.WRITE_MEMORY,
})

_REPAIR_WRITES = frozenset({
    GovernedOperation.APPLY_CODE_CHANGE,
    GovernedOperation.ROLLBACK,
})

_EVOLUTION_WRITES = frozenset({
    GovernedOperation.ADD_TOOL,
    GovernedOperation.CHANGE_POLICY,
    GovernedOperation.ENABLE_EXTERNAL_CHANNEL,
})


class GovernancePolicy:
    """Mode-aware policy for learning, repair and self-evolution."""

    def evaluate(
        self,
        *,
        mode: AgentMode | str,
        operation: GovernedOperation | str,
        evidence_verified: bool = False,
        tests_passed: bool = False,
        has_rollback: bool = False,
    ) -> GovernanceDecision:
        mode = _mode(mode)
        operation = _operation(operation)

        if operation in _READ_ONLY:
            return _decision(mode, operation, "allow", "read-only diagnostic surface")

        if mode is AgentMode.DIAGNOSTIC:
            return _decision(
                mode,
                operation,
                "deny",
                "diagnostic mode may inspect only; no writes or side effects",
            )

        if mode is AgentMode.LEARNING:
            return self._learning(operation, mode, evidence_verified=evidence_verified)

        if mode is AgentMode.REPAIR:
            return self._repair(
                operation,
                mode,
                evidence_verified=evidence_verified,
                tests_passed=tests_passed,
                has_rollback=has_rollback,
            )

        if mode is AgentMode.IMPROVEMENT:
            return self._improvement(
                operation,
                mode,
                evidence_verified=evidence_verified,
                tests_passed=tests_passed,
                has_rollback=has_rollback,
            )

        return self._governance(operation, mode)

    def _learning(
        self,
        operation: GovernedOperation,
        mode: AgentMode,
        *,
        evidence_verified: bool,
    ) -> GovernanceDecision:
        if operation in _MEMORY_WRITES:
            if evidence_verified:
                return _decision(mode, operation, "allow", "verified knowledge may be saved")
            return _decision(
                mode,
                operation,
                "require_approval",
                "unverified knowledge needs human approval before memory write",
            )
        return _decision(mode, operation, "deny", "learning mode cannot modify code or policy")

    def _repair(
        self,
        operation: GovernedOperation,
        mode: AgentMode,
        *,
        evidence_verified: bool,
        tests_passed: bool,
        has_rollback: bool,
    ) -> GovernanceDecision:
        if operation is GovernedOperation.PROPOSE_DIFF:
            if evidence_verified:
                return _decision(mode, operation, "allow", "verified diagnosis may propose a diff")
            return _decision(mode, operation, "require_approval", "diagnosis is not verified")
        if operation is GovernedOperation.APPLY_CODE_CHANGE:
            if evidence_verified and has_rollback:
                return _decision(
                    mode,
                    operation,
                    "require_approval",
                    "repair writes require approval with verified diagnosis and rollback",
                )
            return _decision(
                mode,
                operation,
                "deny",
                "repair write needs verified diagnosis and rollback plan before approval",
            )
        if operation is GovernedOperation.ROLLBACK:
            if has_rollback:
                return _decision(
                    mode,
                    operation,
                    "allow",
                    "rollback is a bounded compensation action",
                )
            return _decision(
                mode,
                operation,
                "deny",
                "rollback needs a registered compensation plan",
            )
        if operation is GovernedOperation.RUN_SHELL:
            return _decision(mode, operation, "require_approval", "shell is always approval-gated")
        return _decision(mode, operation, "deny", "repair mode is limited to fixing a diagnosed fault")

    def _improvement(
        self,
        operation: GovernedOperation,
        mode: AgentMode,
        *,
        evidence_verified: bool,
        tests_passed: bool,
        has_rollback: bool,
    ) -> GovernanceDecision:
        if operation is GovernedOperation.PROPOSE_DIFF:
            return _decision(mode, operation, "allow", "improvement may draft proposals")
        if operation in _EVOLUTION_WRITES or operation is GovernedOperation.APPLY_CODE_CHANGE:
            if evidence_verified and tests_passed and has_rollback:
                return _decision(
                    mode,
                    operation,
                    "require_approval",
                    "self-evolution writes require human approval",
                )
            return _decision(
                mode,
                operation,
                "deny",
                "self-evolution needs evidence, passing tests and rollback before approval",
            )
        if operation is GovernedOperation.RUN_SHELL:
            return _decision(mode, operation, "require_approval", "shell is always approval-gated")
        return _decision(mode, operation, "deny", "operation is outside improvement mode")

    def _governance(
        self,
        operation: GovernedOperation,
        mode: AgentMode,
    ) -> GovernanceDecision:
        if operation in {
            GovernedOperation.CHANGE_POLICY,
            GovernedOperation.ENABLE_EXTERNAL_CHANNEL,
        }:
            return _decision(
                mode,
                operation,
                "require_approval",
                "governance changes require explicit human approval",
            )
        if operation in _READ_ONLY:
            return _decision(mode, operation, "allow", "governance may inspect state")
        return _decision(mode, operation, "deny", "governance mode cannot perform arbitrary work")


def _decision(
    mode: AgentMode,
    operation: GovernedOperation,
    verdict: GovernanceVerdict,
    *reasons: str,
) -> GovernanceDecision:
    return GovernanceDecision(
        mode=mode,
        operation=operation,
        verdict=verdict,
        reasons=tuple(reasons),
    )


def _mode(value: AgentMode | str) -> AgentMode:
    if isinstance(value, AgentMode):
        return value
    return AgentMode(str(value).strip().lower())


def _operation(value: GovernedOperation | str) -> GovernedOperation:
    if isinstance(value, GovernedOperation):
        return value
    return GovernedOperation(str(value).strip().lower())
