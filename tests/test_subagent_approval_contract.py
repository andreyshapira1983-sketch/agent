"""Hermetic approval-boundary tests for canonical subagent contracts."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import main as main_module

from cli.commands_approval import _handle_approval_run
from core.approval_inbox import ApprovalInbox
from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
    approval_payload_from_proposal,
)
from core.subagent_contract_audit import (
    ContractAuditIssue,
    ContractAuditReport,
    SubagentExecutionReceipt,
)
from core.subagent_memory_scope import (
    BudgetScope,
    MemoryScope,
    SubagentProposal,
    ToolScope,
)
from core.subagent_registry import SubagentRegistry
from core.subagent_runner import SubagentContractRefused
from tools.base import ToolRegistry


class _Result:
    status = "success"
    error = ""

    def __init__(self, contract_audit=None, execution_receipt=None) -> None:
        self.contract_audit = contract_audit
        self.execution_receipt = execution_receipt

    def to_evidence_text(self) -> str:
        return "canonical subagent result"


class _SpawnTool:
    name = "spawn_subagent"

    def __init__(self, *, refusal: bool = False, audited: bool = False) -> None:
        self.refusal = refusal
        self.audited = audited
        self.calls = []

    def run_contract(self, contract, *, approved=False, context=""):
        self.calls.append((contract, approved, context))
        if self.refusal:
            raise SubagentContractRefused("memory scope unsupported")
        audit = None
        receipt = None
        if self.audited:
            receipt = SubagentExecutionReceipt(
                contract.contract_id,
                contract.schema_version,
                approval_granted=True,
                used_tools=("file_read",),
                iterations=1,
            )
            audit = ContractAuditReport(
                contract.contract_id,
                contract.schema_version,
                "unknown",
                (
                    ContractAuditIssue(
                        "model_calls_unmeasured",
                        "unknown",
                        "model calls were not measured",
                    ),
                ),
            )
        return _Result(audit, receipt)


def _agent(inbox: ApprovalInbox, tool: _SpawnTool):
    registry = ToolRegistry()
    registry.register(tool)  # type: ignore[arg-type]
    return SimpleNamespace(
        approval_inbox=inbox,
        registry=registry,
        log=MagicMock(),
    )


def _canonical() -> CanonicalSubagentContract:
    return CanonicalSubagentContract(
        contract_id="sap_approved",
        source="proposal",
        source_id="sap_approved",
        name="AuditAgent",
        role="AuditAgent",
        objective="audit one component",
        outputs=("findings",),
        memory_scope=None,
        tool_scope=CanonicalToolScope(allowed_tools=("file_read",), read_only=True),
        budget_scope=CanonicalBudgetScope(
            max_model_calls=2,
            max_iterations=1,
            max_file_writes=0,
        ),
        risk_level="low",
        approval_required=True,
    )


def _legacy_proposal() -> SubagentProposal:
    return SubagentProposal(
        proposal_id="sap_legacy",
        task_goal="audit legacy item",
        why_needed="bounded context",
        proposed_role="LegacyAuditAgent",
        memory_scope=MemoryScope((), (), True),
        tool_scope=ToolScope(("file_read",), ("file_write",), True),
        budget_scope=BudgetScope(2, 0, 0, 1),
        risk_level="low",
        expected_output="findings",
        approval_required=True,
    )


def _approved_item(inbox: ApprovalInbox, payload: dict):
    item = inbox.add(
        operation="launch_subagent",
        summary="approved subagent",
        payload=payload,
    )
    return inbox.approve(item.id)


def test_approved_canonical_subagent_executes_and_records(tmp_path: Path, capsys) -> None:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approvals.jsonl")
    contract = _canonical()
    item = _approved_item(inbox, {"canonical_contract": contract.to_dict()})
    tool = _SpawnTool(audited=True)
    agent = _agent(inbox, tool)

    assert _handle_approval_run(item.id, agent, tmp_path) is True

    assert inbox.get(item.id).status == "executed"
    assert tool.calls == [(contract, True, "")]
    assert "canonical subagent result" in capsys.readouterr().err
    record = SubagentRegistry.load(tmp_path).contract_runs["sap_approved@v1"]
    assert record.executed == 1
    assert record.audit_unknown == 1
    assert record.last_execution_receipt["approval_granted"] is True
    assert record.last_audit_report["verdict"] == "unknown"


def test_policy_refusal_keeps_item_approved_and_records_refusal(
    tmp_path: Path,
    capsys,
) -> None:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approvals.jsonl")
    contract = _canonical()
    item = _approved_item(inbox, {"canonical_contract": contract.to_dict()})
    agent = _agent(inbox, _SpawnTool(refusal=True))

    assert _handle_approval_run(item.id, agent, tmp_path) is True

    assert inbox.get(item.id).status == "approved"
    assert "approval run refused" in capsys.readouterr().err
    record = SubagentRegistry.load(tmp_path).contract_runs["sap_approved@v1"]
    assert record.refused == 1


def test_legacy_flat_proposal_payload_still_executes(tmp_path: Path) -> None:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approvals.jsonl")
    item = _approved_item(inbox, _legacy_proposal().to_dict())
    tool = _SpawnTool()
    agent = _agent(inbox, tool)

    assert _handle_approval_run(item.id, agent, tmp_path) is True

    assert inbox.get(item.id).status == "executed"
    assert tool.calls[0][0].contract_id == "sap_legacy"


def test_invalid_subagent_payload_never_executes(tmp_path: Path, capsys) -> None:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approvals.jsonl")
    item = _approved_item(inbox, {"canonical_contract": {"bad": "shape"}})
    tool = _SpawnTool()
    agent = _agent(inbox, tool)

    assert _handle_approval_run(item.id, agent, tmp_path) is True

    assert inbox.get(item.id).status == "approved"
    assert tool.calls == []
    assert "invalid subagent payload" in capsys.readouterr().err


def test_submit_command_persists_versioned_canonical_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approvals.jsonl")
    proposal = _legacy_proposal()
    result = SimpleNamespace(
        ok=True,
        proposal=proposal,
        user_summary=lambda: "proposal ready",
    )
    monkeypatch.setattr(main_module, "propose_subagent", lambda *args, **kwargs: result)
    agent = SimpleNamespace(
        model_router=MagicMock(),
        logger=MagicMock(),
        approval_inbox=inbox,
    )

    assert main_module._handle_subagent_proposal(
        "audit legacy item --submit",
        agent,
        tmp_path,
    ) is True

    item = inbox.pending()[0]
    assert item.operation == "launch_subagent"
    assert item.payload["payload_schema"] == "canonical_subagent_approval/v1"
    canonical = CanonicalSubagentContract.from_dict(
        item.payload["canonical_contract"]
    )
    assert canonical.contract_id == proposal.proposal_id
