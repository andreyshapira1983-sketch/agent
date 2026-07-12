"""Hermetic contract-version ledger tests for SubagentRegistry."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_contract_audit import (
    ContractAuditIssue,
    ContractAuditReport,
    SubagentExecutionReceipt,
)
from core.subagent_registry import DEFAULT_ROLES, REGISTRY_PATH, SubagentRegistry


def _contract(**overrides) -> CanonicalSubagentContract:
    values = {
        "contract_id": "subc_fixed",
        "source": "team_plan",
        "name": "AuditAgent",
        "role": "auditor",
        "objective": "Audit one component",
        "outputs": ("findings",),
        "tool_scope": CanonicalToolScope(allowed_tools=("file_read",)),
        "budget_scope": CanonicalBudgetScope(
            max_model_calls=2,
            max_iterations=1,
            max_cost_units=3,
        ),
        "risk_level": "low",
        "approval_required": False,
    }
    values.update(overrides)
    return CanonicalSubagentContract(**values)


def test_contract_outcomes_are_recorded_without_mutating_role_scores(
    tmp_path: Path,
) -> None:
    registry = SubagentRegistry.load(tmp_path)
    roles_before = {key: value.to_dict() for key, value in registry.roles.items()}
    contract = _contract()

    assert registry.record_contract_run(contract, "executed", save=False) is True
    assert registry.record_contract_run(contract, "error", save=False) is True
    assert registry.record_contract_run(contract, "refused", save=False) is True

    record = registry.contract_runs["subc_fixed@v1"]
    assert record.invocations == 3
    assert record.executed == 1
    assert record.errors == 1
    assert record.refused == 1
    assert record.last_outcome == "refused"
    assert {key: value.to_dict() for key, value in registry.roles.items()} == roles_before


def test_contract_ledger_persists_and_reloads(tmp_path: Path) -> None:
    registry = SubagentRegistry.load(tmp_path)
    registry.record_contract_run(_contract(), "executed")

    reloaded = SubagentRegistry.load(tmp_path)
    record = reloaded.contract_runs["subc_fixed@v1"]
    assert record.contract_id == "subc_fixed"
    assert record.schema_version == 1
    assert record.role_id == "auditor"
    assert record.source == "team_plan"
    assert record.executed == 1


def test_audit_report_is_counted_and_persisted_without_role_reputation(
    tmp_path: Path,
) -> None:
    registry = SubagentRegistry.load(tmp_path)
    contract = _contract()
    roles_before = {key: value.to_dict() for key, value in registry.roles.items()}
    audit = ContractAuditReport(
        contract_id=contract.contract_id,
        schema_version=contract.schema_version,
        verdict="unknown",
        issues=(
            ContractAuditIssue(
                "model_calls_unmeasured",
                "unknown",
                "model calls were not measured",
                2,
                None,
            ),
        ),
    )
    receipt = SubagentExecutionReceipt(
        contract.contract_id,
        contract.schema_version,
        used_tools=("file_read",),
        model_calls=None,
        iterations=1,
    )

    registry.record_contract_run(
        contract,
        "executed",
        execution_receipt=receipt,
        audit_report=audit,
    )

    reloaded = SubagentRegistry.load(tmp_path)
    record = reloaded.contract_runs["subc_fixed@v1"]
    assert record.audit_unknown == 1
    assert record.audit_passes == 0
    assert record.audit_failures == 0
    assert record.last_execution_receipt == receipt.to_dict()
    assert record.last_audit_report == audit.to_dict()
    assert {key: value.to_dict() for key, value in reloaded.roles.items()} == roles_before


def test_mismatched_audit_identity_is_rejected_without_recording(tmp_path: Path) -> None:
    registry = SubagentRegistry.load(tmp_path)
    contract = _contract()
    audit = ContractAuditReport("different", 1, "pass", ())

    with pytest.raises(ValueError, match="audit report identity"):
        registry.record_contract_run(
            contract,
            "executed",
            audit_report=audit,
            save=False,
        )

    assert registry.contract_runs == {}


def test_mismatched_receipt_identity_is_rejected_without_recording(
    tmp_path: Path,
) -> None:
    registry = SubagentRegistry.load(tmp_path)
    receipt = SubagentExecutionReceipt("different", 1)

    with pytest.raises(ValueError, match="execution receipt identity"):
        registry.record_contract_run(
            _contract(),
            "executed",
            execution_receipt=receipt,
            save=False,
        )

    assert registry.contract_runs == {}


def test_old_registry_without_contract_runs_loads_unchanged(tmp_path: Path) -> None:
    path = tmp_path / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"roles": {"builder": {"invocations": 2, "successes": 1}}}),
        encoding="utf-8",
    )

    registry = SubagentRegistry.load(tmp_path)

    assert registry.contract_runs == {}
    assert registry.roles["builder"].invocations == 2
    assert registry.roles["builder"].successes == 1
    assert set(DEFAULT_ROLES).issubset(registry.roles)


def test_loader_keeps_schema_versions_in_separate_records(tmp_path: Path) -> None:
    path = tmp_path / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "contract_id": "shared_id",
        "role_id": "auditor",
        "source": "team_plan",
        "invocations": 1,
        "executed": 1,
    }
    path.write_text(
        json.dumps(
            {
                "roles": {},
                "contract_runs": {
                    "shared_id@v1": {**base, "schema_version": 1},
                    "shared_id@v2": {**base, "schema_version": 2},
                },
            }
        ),
        encoding="utf-8",
    )

    registry = SubagentRegistry.load(tmp_path)

    assert set(registry.contract_runs) == {"shared_id@v1", "shared_id@v2"}


def test_reused_identity_with_different_role_is_rejected(tmp_path: Path) -> None:
    registry = SubagentRegistry.load(tmp_path)
    registry.record_contract_run(_contract(), "executed", save=False)

    with pytest.raises(ValueError, match="identity collision"):
        registry.record_contract_run(
            _contract(role="different_role"),
            "executed",
            save=False,
        )


def test_unknown_outcome_is_ignored_without_writing(tmp_path: Path) -> None:
    registry = SubagentRegistry.load(tmp_path)

    assert registry.record_contract_run(_contract(), "verified") is False
    assert registry.contract_runs == {}
    assert not (tmp_path / REGISTRY_PATH).exists()


def test_status_report_exposes_contract_ledger_read_only(tmp_path: Path) -> None:
    registry = SubagentRegistry.load(tmp_path)
    registry.record_contract_run(_contract(), "executed")
    before = registry.path.read_text(encoding="utf-8")

    report = registry.status_report()

    assert report["contract_count"] == 1
    assert report["contract_runs"][0]["contract_id"] == "subc_fixed"
    assert registry.path.read_text(encoding="utf-8") == before
