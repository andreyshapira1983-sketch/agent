"""Hermetic tests for the canonical SubAgentRunner entry point."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalMemoryScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_runner import (
    SubAgentRunResult,
    SubAgentRunner,
    SubagentContractRefused,
)
from tools.base import ToolRegistry


def _runner(tmp_path: Path) -> SubAgentRunner:
    return SubAgentRunner(
        workspace_root=tmp_path,
        policy=MagicMock(),
        model_router=MagicMock(),
        parent_registry=ToolRegistry(),
        log_dir=tmp_path,
    )


def _contract(**overrides) -> CanonicalSubagentContract:
    values = {
        "contract_id": "subc_fixed",
        "source": "team_plan",
        "name": "RepositoryAuditor",
        "role": "auditor",
        "objective": "Audit the repository",
        "outputs": ("findings",),
        "tool_scope": CanonicalToolScope(allowed_tools=("file_read",)),
        "budget_scope": CanonicalBudgetScope(
            max_model_calls=2,
            max_iterations=1,
            max_cost_units=3,
        ),
        "model_role": "verifier",
        "risk_level": "low",
        "approval_required": False,
    }
    values.update(overrides)
    return CanonicalSubagentContract(**values)


def test_run_contract_maps_canonical_fields_to_legacy_runner(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    expected = object()
    runner.run = MagicMock(return_value=expected)  # type: ignore[method-assign]

    result = runner.run_contract(_contract(), context="bounded context")

    assert result is expected
    runner.run.assert_called_once_with(
        contract_name="RepositoryAuditor",
        role="auditor",
        objective="Audit the repository",
        context="bounded context",
        allowed_tools=["file_read"],
        model_role="verifier",
    )


def test_run_contract_requires_explicit_approval(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.run = MagicMock()  # type: ignore[method-assign]
    contract = _contract(approval_required=True)

    with pytest.raises(SubagentContractRefused, match="requires human approval"):
        runner.run_contract(contract)
    runner.run.assert_not_called()

    runner.run_contract(contract, approved=True)
    runner.run.assert_called_once()


def test_run_contract_builds_receipt_and_audit_from_child_trace(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    trace_id = "trace_contract"
    (tmp_path / f"{trace_id}.jsonl").write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "event": "tool_call",
                        "payload": {"tool_name": "file_read"},
                    }
                ),
                json.dumps(
                    {
                        "event": "respond",
                        "payload": {"attempts_used": 1},
                    }
                ),
            )
        ),
        encoding="utf-8",
    )
    legacy_result = SubAgentRunResult(
        contract_name="RepositoryAuditor",
        role="auditor",
        objective="Audit the repository",
        answer="done",
        trace_id=trace_id,
        status="success",
    )
    runner.run = MagicMock(return_value=legacy_result)  # type: ignore[method-assign]

    result = runner.run_contract(_contract())

    assert result.execution_receipt is not None
    assert result.execution_receipt.used_tools == ("file_read",)
    assert result.execution_receipt.iterations == 1
    assert result.execution_receipt.file_writes == 0
    assert result.execution_receipt.memory_read_tags == ()
    assert result.execution_receipt.model_calls is None
    assert result.contract_audit is not None
    assert result.contract_audit.verdict == "unknown"
    assert {issue.code for issue in result.contract_audit.issues} >= {
        "memory_scope_undeclared",
        "model_calls_unmeasured",
        "cost_units_unmeasured",
    }


def test_malformed_child_trace_degrades_observations_to_unknown(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    trace_id = "trace_broken"
    (tmp_path / f"{trace_id}.jsonl").write_text("not-json", encoding="utf-8")
    runner.run = MagicMock(  # type: ignore[method-assign]
        return_value=SubAgentRunResult(
            contract_name="RepositoryAuditor",
            role="auditor",
            objective="Audit the repository",
            answer="",
            trace_id=trace_id,
            status="error",
            error="child failed",
        )
    )

    result = runner.run_contract(_contract())

    assert result.execution_receipt is not None
    assert result.execution_receipt.used_tools is None
    assert result.execution_receipt.iterations is None
    assert result.contract_audit is not None
    assert result.contract_audit.verdict == "unknown"


@pytest.mark.parametrize(
    "contract, message",
    [
        (
            _contract(
                memory_scope=CanonicalMemoryScope(read_tags=("project",))
            ),
            "persistent memory scope",
        ),
        (
            _contract(
                budget_scope=CanonicalBudgetScope(
                    max_model_calls=2,
                    max_iterations=2,
                    max_cost_units=3,
                )
            ),
            "exactly one iteration",
        ),
        (
            _contract(
                budget_scope=CanonicalBudgetScope(
                    max_model_calls=0,
                    max_iterations=1,
                    max_cost_units=0,
                )
            ),
            "requires at least one model call",
        ),
        (
            _contract(
                budget_scope=CanonicalBudgetScope(
                    max_model_calls=2,
                    max_iterations=1,
                    max_cost_units=3,
                    max_file_writes=1,
                )
            ),
            "does not permit file writes",
        ),
    ],
)
def test_run_contract_refuses_policy_runner_cannot_enforce(
    tmp_path: Path,
    contract: CanonicalSubagentContract,
    message: str,
) -> None:
    runner = _runner(tmp_path)
    runner.run = MagicMock()  # type: ignore[method-assign]

    with pytest.raises(SubagentContractRefused, match=message):
        runner.run_contract(contract, approved=True)
    runner.run.assert_not_called()
