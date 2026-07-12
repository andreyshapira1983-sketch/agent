from dataclasses import replace

import pytest

from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalMemoryScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_contract_audit import (
    SubagentExecutionReceipt,
    audit_subagent_execution,
)


def _contract(**overrides: object) -> CanonicalSubagentContract:
    values: dict[str, object] = {
        "contract_id": "subc_audit",
        "source": "proposal",
        "name": "reader",
        "role": "reader",
        "objective": "inspect evidence",
        "outputs": ("report",),
        "memory_scope": CanonicalMemoryScope(read_tags=("project",)),
        "tool_scope": CanonicalToolScope(
            allowed_tools=("file_read",),
            forbidden_tools=("shell",),
            read_only=True,
        ),
        "budget_scope": CanonicalBudgetScope(
            max_model_calls=1,
            max_iterations=1,
            max_cost_units=3,
            max_web_fetches=0,
            max_file_writes=0,
        ),
        "risk_level": "low",
        "approval_required": True,
    }
    values.update(overrides)
    return CanonicalSubagentContract(**values)  # type: ignore[arg-type]


def _receipt(**overrides: object) -> SubagentExecutionReceipt:
    values: dict[str, object] = {
        "contract_id": "subc_audit",
        "schema_version": 1,
        "approval_granted": True,
        "used_tools": ("file_read",),
        "memory_read_tags": ("project",),
        "memory_write_tags": (),
        "model_calls": 1,
        "iterations": 1,
        "cost_units": 2,
        "web_fetches": 0,
        "file_writes": 0,
    }
    values.update(overrides)
    return SubagentExecutionReceipt(**values)  # type: ignore[arg-type]


def _codes(report: object) -> set[str]:
    return {issue.code for issue in report.issues}  # type: ignore[attr-defined]


def test_fully_measured_compliant_execution_passes() -> None:
    report = audit_subagent_execution(_contract(), _receipt())

    assert report.verdict == "pass"
    assert report.issues == ()
    assert report.to_dict()["verdict"] == "pass"


def test_missing_telemetry_is_unknown_not_pass() -> None:
    receipt = SubagentExecutionReceipt("subc_audit", 1, approval_granted=True)

    report = audit_subagent_execution(_contract(), receipt)

    assert report.verdict == "unknown"
    assert "tools_unmeasured" in _codes(report)
    assert "model_calls_unmeasured" in _codes(report)
    assert all(issue.kind == "unknown" for issue in report.issues)


def test_any_violation_makes_report_fail_even_with_unknowns() -> None:
    report = audit_subagent_execution(
        _contract(),
        _receipt(approval_granted=False, used_tools=None),
    )

    assert report.verdict == "fail"
    assert {"approval_missing", "tools_unmeasured"} <= _codes(report)


@pytest.mark.parametrize(
    ("used_tools", "expected_code"),
    [
        (("shell",), "forbidden_tool_used"),
        (("network",), "tool_not_allowed"),
    ],
)
def test_tool_policy_violations_fail(
    used_tools: tuple[str, ...], expected_code: str
) -> None:
    report = audit_subagent_execution(_contract(), _receipt(used_tools=used_tools))

    assert report.verdict == "fail"
    assert expected_code in _codes(report)


def test_budget_and_read_only_overruns_fail() -> None:
    report = audit_subagent_execution(
        _contract(),
        _receipt(model_calls=2, cost_units=4, file_writes=1),
    )

    assert report.verdict == "fail"
    assert {
        "model_calls_exceeded",
        "cost_units_exceeded",
        "file_writes_exceeded",
        "read_only_violated",
    } <= _codes(report)


def test_memory_use_outside_declared_scope_fails() -> None:
    report = audit_subagent_execution(
        _contract(),
        _receipt(memory_read_tags=("secret",), memory_write_tags=("project",)),
    )

    assert report.verdict == "fail"
    assert {
        "memory_read_outside_scope",
        "memory_write_outside_scope",
    } <= _codes(report)


def test_undeclared_memory_policy_is_unknown() -> None:
    report = audit_subagent_execution(
        _contract(memory_scope=None),
        _receipt(memory_read_tags=(), memory_write_tags=()),
    )

    assert report.verdict == "unknown"
    assert _codes(report) == {"memory_scope_undeclared"}


@pytest.mark.parametrize(
    ("receipt_changes", "expected_code"),
    [
        ({"verifier_status": "failed", "stop_reason": "done"}, "verifier_not_passed"),
        ({"verifier_status": "passed", "stop_reason": "timeout"}, "stop_condition_not_declared"),
    ],
)
def test_verifier_and_stop_policy_failures(
    receipt_changes: dict[str, object], expected_code: str
) -> None:
    contract = _contract(verifier="evidence_check", stop_conditions=("done",))
    report = audit_subagent_execution(contract, _receipt(**receipt_changes))

    assert report.verdict == "fail"
    assert expected_code in _codes(report)


def test_unmeasured_verifier_and_stop_reason_are_unknown() -> None:
    contract = _contract(verifier="evidence_check", stop_conditions=("done",))

    report = audit_subagent_execution(contract, _receipt())

    assert report.verdict == "unknown"
    assert {"verifier_unmeasured", "stop_reason_unmeasured"} <= _codes(report)


def test_receipt_identity_mismatch_fails() -> None:
    receipt = replace(_receipt(), contract_id="other", schema_version=2)

    report = audit_subagent_execution(_contract(), receipt)

    assert report.verdict == "fail"
    assert {"contract_id_mismatch", "schema_version_mismatch"} <= _codes(report)


def test_receipt_rejects_negative_counters() -> None:
    with pytest.raises(ValueError, match="model_calls must be >= 0"):
        _receipt(model_calls=-1)
