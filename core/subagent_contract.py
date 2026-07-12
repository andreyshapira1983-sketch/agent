"""Canonical subagent contract bridge.

This module is the first compatibility step toward one end-to-end subagent
contract.  It intentionally does not change proposal, planning, execution, or
registry behaviour yet.  Instead it provides a loss-aware canonical shape and
deterministic adapters for the two legacy contract models:

* :class:`core.subagent_memory_scope.SubagentProposal`
* :class:`core.team_plan.SubagentContract`

Fields that a legacy model never declared remain ``None`` rather than being
silently invented.  Later migration stages can therefore distinguish an
explicit zero/empty policy from an undeclared policy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from core.subagent_memory_scope import SubagentProposal
    from core.team_plan import SubagentContract


CONTRACT_SCHEMA_VERSION = 1

ContractSource = Literal["proposal", "team_plan"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class CanonicalMemoryScope:
    """Persistent-memory rights declared for one subagent contract."""

    read_tags: tuple[str, ...] = ()
    write_tags: tuple[str, ...] = ()
    write_requires_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_tags": list(self.read_tags),
            "write_tags": list(self.write_tags),
            "write_requires_review": self.write_requires_review,
        }


@dataclass(frozen=True)
class CanonicalToolScope:
    """Tool allow/deny policy declared for one subagent contract."""

    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    read_only: bool | None = None

    def __post_init__(self) -> None:
        overlap = set(self.allowed_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(
                f"tools cannot be both allowed and forbidden: {sorted(overlap)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class CanonicalBudgetScope:
    """All currently known execution limits, preserving undeclared values."""

    max_model_calls: int
    max_iterations: int
    max_cost_units: int | None = None
    max_web_fetches: int | None = None
    max_file_writes: int | None = None

    def __post_init__(self) -> None:
        values = {
            "max_model_calls": self.max_model_calls,
            "max_iterations": self.max_iterations,
            "max_cost_units": self.max_cost_units,
            "max_web_fetches": self.max_web_fetches,
            "max_file_writes": self.max_file_writes,
        }
        for name, value in values.items():
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_iterations": self.max_iterations,
            "max_cost_units": self.max_cost_units,
            "max_web_fetches": self.max_web_fetches,
            "max_file_writes": self.max_file_writes,
        }


@dataclass(frozen=True)
class CanonicalSubagentContract:
    """Versioned, loss-aware contract shared by future subagent layers.

    The adapters are deliberately one-way during this first stage.  Existing
    call sites continue using their legacy classes, so introducing this module
    cannot change runtime behaviour.
    """

    contract_id: str
    source: ContractSource
    name: str
    role: str
    objective: str
    outputs: tuple[str, ...]
    tool_scope: CanonicalToolScope
    budget_scope: CanonicalBudgetScope
    risk_level: RiskLevel
    approval_required: bool
    schema_version: int = CONTRACT_SCHEMA_VERSION
    source_id: str | None = None
    why_needed: str = ""
    inputs: tuple[str, ...] = ()
    memory_scope: CanonicalMemoryScope | None = None
    model_role: str | None = None
    verifier: str | None = None
    stop_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("contract_id", "name", "role", "objective"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported contract schema version: {self.schema_version}"
            )
        if self.risk_level not in {"low", "medium", "high"}:
            raise ValueError("risk_level must be low, medium, or high")

    @classmethod
    def from_proposal(
        cls, proposal: "SubagentProposal"
    ) -> "CanonicalSubagentContract":
        """Adapt a proposal without inventing team-planner-only policy."""
        return cls(
            contract_id=proposal.proposal_id,
            source="proposal",
            source_id=proposal.proposal_id,
            name=proposal.proposed_role,
            role=proposal.proposed_role,
            objective=proposal.task_goal,
            why_needed=proposal.why_needed,
            outputs=(proposal.expected_output,),
            memory_scope=CanonicalMemoryScope(
                read_tags=proposal.memory_scope.read_tags,
                write_tags=proposal.memory_scope.write_tags,
                write_requires_review=proposal.memory_scope.write_requires_review,
            ),
            tool_scope=CanonicalToolScope(
                allowed_tools=proposal.tool_scope.allowed_tools,
                forbidden_tools=proposal.tool_scope.forbidden_tools,
                read_only=proposal.tool_scope.read_only,
            ),
            budget_scope=CanonicalBudgetScope(
                max_model_calls=proposal.budget_scope.max_model_calls,
                max_iterations=proposal.budget_scope.max_cycles,
                max_web_fetches=proposal.budget_scope.max_web_fetches,
                max_file_writes=proposal.budget_scope.max_file_writes,
            ),
            risk_level=proposal.risk_level,
            approval_required=proposal.approval_required,
        )

    @classmethod
    def from_team_contract(
        cls, contract: "SubagentContract"
    ) -> "CanonicalSubagentContract":
        """Adapt a team-plan contract without fabricating memory policy."""
        source_payload = contract.to_dict()
        return cls(
            contract_id=_deterministic_contract_id(source_payload),
            source="team_plan",
            name=contract.name,
            role=contract.role,
            objective=contract.objective,
            inputs=contract.inputs,
            outputs=contract.outputs,
            memory_scope=None,
            tool_scope=CanonicalToolScope(
                allowed_tools=contract.allowed_tools,
                forbidden_tools=contract.forbidden_tools,
                read_only=None,
            ),
            budget_scope=CanonicalBudgetScope(
                max_model_calls=contract.max_model_calls,
                max_iterations=contract.max_iterations,
                max_cost_units=contract.max_cost_units,
            ),
            model_role=contract.model_role,
            verifier=contract.verifier,
            stop_conditions=contract.stop_conditions,
            risk_level=contract.risk_level,
            approval_required=contract.approval_required,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalSubagentContract":
        """Parse a persisted canonical contract with strict type checks."""
        if not isinstance(data, dict):
            raise ValueError("canonical contract must be an object")
        source = str(data.get("source") or "")
        if source not in {"proposal", "team_plan"}:
            raise ValueError("canonical contract source is invalid")

        tool_data = _mapping(data.get("tool_scope"), "tool_scope")
        budget_data = _mapping(data.get("budget_scope"), "budget_scope")
        memory_raw = data.get("memory_scope")
        memory_scope = None
        if memory_raw is not None:
            memory_data = _mapping(memory_raw, "memory_scope")
            memory_scope = CanonicalMemoryScope(
                read_tags=_string_tuple(memory_data.get("read_tags"), "read_tags"),
                write_tags=_string_tuple(memory_data.get("write_tags"), "write_tags"),
                write_requires_review=_boolean(
                    memory_data.get("write_requires_review"),
                    "write_requires_review",
                ),
            )

        return cls(
            schema_version=_integer(data.get("schema_version"), "schema_version"),
            contract_id=str(data.get("contract_id") or ""),
            source=source,  # type: ignore[arg-type]
            source_id=_optional_string(data.get("source_id"), "source_id"),
            name=str(data.get("name") or ""),
            role=str(data.get("role") or ""),
            objective=str(data.get("objective") or ""),
            why_needed=str(data.get("why_needed") or ""),
            inputs=_string_tuple(data.get("inputs"), "inputs"),
            outputs=_string_tuple(data.get("outputs"), "outputs"),
            memory_scope=memory_scope,
            tool_scope=CanonicalToolScope(
                allowed_tools=_string_tuple(
                    tool_data.get("allowed_tools"), "allowed_tools"
                ),
                forbidden_tools=_string_tuple(
                    tool_data.get("forbidden_tools"), "forbidden_tools"
                ),
                read_only=_optional_boolean(tool_data.get("read_only"), "read_only"),
            ),
            budget_scope=CanonicalBudgetScope(
                max_model_calls=_integer(
                    budget_data.get("max_model_calls"), "max_model_calls"
                ),
                max_iterations=_integer(
                    budget_data.get("max_iterations"), "max_iterations"
                ),
                max_cost_units=_optional_integer(
                    budget_data.get("max_cost_units"), "max_cost_units"
                ),
                max_web_fetches=_optional_integer(
                    budget_data.get("max_web_fetches"), "max_web_fetches"
                ),
                max_file_writes=_optional_integer(
                    budget_data.get("max_file_writes"), "max_file_writes"
                ),
            ),
            model_role=_optional_string(data.get("model_role"), "model_role"),
            verifier=_optional_string(data.get("verifier"), "verifier"),
            stop_conditions=_string_tuple(
                data.get("stop_conditions"), "stop_conditions"
            ),
            risk_level=str(data.get("risk_level") or ""),  # type: ignore[arg-type]
            approval_required=_boolean(
                data.get("approval_required"), "approval_required"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "source": self.source,
            "source_id": self.source_id,
            "name": self.name,
            "role": self.role,
            "objective": self.objective,
            "why_needed": self.why_needed,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "memory_scope": (
                self.memory_scope.to_dict() if self.memory_scope is not None else None
            ),
            "tool_scope": self.tool_scope.to_dict(),
            "budget_scope": self.budget_scope.to_dict(),
            "model_role": self.model_role,
            "verifier": self.verifier,
            "stop_conditions": list(self.stop_conditions),
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
        }


def _deterministic_contract_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"subc_{digest}"


def approval_payload_from_proposal(proposal: "SubagentProposal") -> dict[str, Any]:
    """Build the versioned approval payload while retaining legacy detail."""
    canonical = CanonicalSubagentContract.from_proposal(proposal)
    return {
        "payload_schema": "canonical_subagent_approval/v1",
        "proposal": proposal.to_dict(),
        "canonical_contract": canonical.to_dict(),
    }


def canonical_from_approval_payload(
    payload: dict[str, Any],
) -> CanonicalSubagentContract:
    """Load current or legacy ``launch_subagent`` approval payloads."""
    if not isinstance(payload, dict):
        raise ValueError("launch_subagent payload must be an object")
    canonical = payload.get("canonical_contract")
    if canonical is not None:
        return CanonicalSubagentContract.from_dict(
            _mapping(canonical, "canonical_contract")
        )

    legacy = payload.get("proposal", payload)
    legacy = _mapping(legacy, "proposal")
    memory = _mapping(legacy.get("memory_scope"), "memory_scope")
    tools = _mapping(legacy.get("tool_scope"), "tool_scope")
    budget = _mapping(legacy.get("budget_scope"), "budget_scope")
    proposal_id = str(legacy.get("proposal_id") or "")
    role = str(legacy.get("proposed_role") or "")
    return CanonicalSubagentContract(
        contract_id=proposal_id,
        source="proposal",
        source_id=proposal_id,
        name=role,
        role=role,
        objective=str(legacy.get("task_goal") or ""),
        why_needed=str(legacy.get("why_needed") or ""),
        outputs=(str(legacy.get("expected_output") or ""),),
        memory_scope=CanonicalMemoryScope(
            read_tags=_string_tuple(memory.get("read_tags"), "read_tags"),
            write_tags=_string_tuple(memory.get("write_tags"), "write_tags"),
            write_requires_review=_boolean(
                memory.get("write_requires_review"), "write_requires_review"
            ),
        ),
        tool_scope=CanonicalToolScope(
            allowed_tools=_string_tuple(tools.get("allowed_tools"), "allowed_tools"),
            forbidden_tools=_string_tuple(
                tools.get("forbidden_tools"), "forbidden_tools"
            ),
            read_only=_optional_boolean(tools.get("read_only"), "read_only"),
        ),
        budget_scope=CanonicalBudgetScope(
            max_model_calls=_integer(
                budget.get("max_model_calls"), "max_model_calls"
            ),
            max_iterations=_integer(budget.get("max_cycles"), "max_cycles"),
            max_web_fetches=_optional_integer(
                budget.get("max_web_fetches"), "max_web_fetches"
            ),
            max_file_writes=_optional_integer(
                budget.get("max_file_writes"), "max_file_writes"
            ),
        ),
        risk_level=str(legacy.get("risk_level") or ""),  # type: ignore[arg-type]
        approval_required=_boolean(
            legacy.get("approval_required"), "approval_required"
        ),
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} entries must be strings")
    return tuple(value)


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_integer(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name)


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _optional_boolean(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, name)


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value
