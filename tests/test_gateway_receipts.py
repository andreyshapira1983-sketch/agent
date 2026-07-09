"""Gateway slice G4 — gateway decision receipts (kind="gateway")."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.tool_receipts as tool_receipts
from core.actuation_gateway import ActuationGateway, GatewayDecision
from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
    run_approved_self_apply,
)
from core.self_apply_lane import SelfApplyReport
from core.tool_receipts import (
    ToolReceiptLedger,
    default_receipts_path,
    record_gateway_receipt,
)
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _gateway_receipts(ws: Path) -> list:
    return [
        r
        for r in ToolReceiptLedger(default_receipts_path(ws)).load()
        if r.kind == "gateway"
    ]


# ── unit: record_gateway_receipt ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome", ["allow", "simulate", "deny", "escalate", "passthrough"]
)
def test_record_gateway_receipt_writes_kind_and_operation(
    workspace: Path, outcome: str
) -> None:
    decision = GatewayDecision(outcome=outcome, tool_name="file_write", path="repl")
    out = record_gateway_receipt(decision, workspace=workspace)

    assert out is not None
    rows = _gateway_receipts(workspace)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "gateway"
    assert row.operation == f"gateway.{outcome}"
    assert row.refs.get("tool_name") == "file_write"
    assert row.refs.get("gateway_path") == "repl"


def test_record_gateway_receipt_no_raw_secret_or_payload(workspace: Path) -> None:
    secret = "sk-GATEWAY-SECRET-9999"
    decision = GatewayDecision(
        outcome="deny",
        tool_name="file_write",
        path="repl",
        reasons=(f"blocked because token {secret} present",),
    )
    record_gateway_receipt(decision, workspace=workspace)

    raw = default_receipts_path(workspace).read_text(encoding="utf-8")
    assert secret not in raw
    for r in _gateway_receipts(workspace):
        assert secret not in json.dumps(r.to_dict(), ensure_ascii=False)


def test_record_gateway_receipt_never_raises_on_append_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tool_receipts, "append_receipt", _boom)
    decision = GatewayDecision(outcome="allow", tool_name="file_write", path="repl")
    assert record_gateway_receipt(decision, workspace=workspace) is None


def test_record_gateway_receipt_no_workspace_returns_none(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    decision = GatewayDecision(outcome="allow", tool_name="file_write", path="repl")
    assert record_gateway_receipt(decision) is None


# ── self-apply path (G3 + G4) ────────────────────────────────────────────────


class _RecordingLane:
    def __init__(self, report: SelfApplyReport):
        self.report = report
        self.calls: list[dict] = []

    def __call__(self, proposal, **kwargs):
        self.calls.append({"proposal": proposal, **kwargs})
        return self.report


class _FakeGateway:
    def __init__(self, outcome: str):
        self.outcome = outcome

    def evaluate_self_apply(self, *, operation: str = SELF_APPLY_OPERATION) -> GatewayDecision:
        return GatewayDecision(outcome=self.outcome, tool_name=operation, path="self_apply")


class _BoomGateway:
    def evaluate_self_apply(self, *, operation: str = SELF_APPLY_OPERATION):
        raise RuntimeError("gateway exploded")


def _payload() -> dict:
    return build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example",
        test_paths=["tests/test_example.py"],
        origin="repair",
    )


def _approved(inbox: ApprovalInbox) -> str:
    item = inbox.add(
        operation=SELF_APPLY_OPERATION, summary="s", risk="reversible", payload=_payload()
    )
    return inbox.approve(item.id).id


def _committed() -> SelfApplyReport:
    return SelfApplyReport(
        status="committed_local", branch="b", files_changed=["core/example.py"], commit_hash="abc"
    )


def _run(inbox, item_id, workspace, lane, **kwargs):
    return run_approved_self_apply(
        inbox=inbox,
        item_id=item_id,
        workspace=workspace,
        vcs=object(),
        test_runner=object(),
        lane=lane,
        **kwargs,
    )


def test_self_apply_allow_writes_receipt_and_runs_lane(workspace: Path) -> None:
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    item_id = _approved(inbox)
    lane = _RecordingLane(_committed())

    result = _run(inbox, item_id, workspace, lane, gateway=_FakeGateway("allow"))

    assert result["status"] == "committed_local"
    assert len(lane.calls) == 1
    rows = _gateway_receipts(workspace)
    assert any(r.operation == "gateway.allow" for r in rows)
    assert any(r.refs.get("proposal_id") == item_id for r in rows)


def test_self_apply_simulate_writes_receipt_no_lane(workspace: Path) -> None:
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run on simulate")

    result = _run(inbox, item_id, workspace, _raising_lane, dry_run=True)

    assert result["status"] == "simulated"
    assert any(r.operation == "gateway.simulate" for r in _gateway_receipts(workspace))


@pytest.mark.parametrize("outcome", ["deny", "block", "escalate"])
def test_self_apply_blocked_writes_receipt_no_lane(workspace: Path, outcome: str) -> None:
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError(f"lane must not run on {outcome}")

    result = _run(inbox, item_id, workspace, _raising_lane, gateway=_FakeGateway(outcome))

    assert result["status"] == "gateway_blocked"
    assert any(r.operation == f"gateway.{outcome}" for r in _gateway_receipts(workspace))
    assert inbox.get(item_id).status == "approved"


def test_self_apply_gateway_exception_fails_closed_receipt_best_effort(
    workspace: Path,
) -> None:
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run when gateway raises")

    result = _run(inbox, item_id, workspace, _raising_lane, gateway=_BoomGateway())

    # Fail-closed behavior is what matters; the error receipt is best-effort.
    assert result["status"] == "gateway_error"
    assert inbox.get(item_id).status == "approved"
    err_rows = [r for r in _gateway_receipts(workspace) if r.status == "error"]
    assert all(r.kind == "gateway" for r in err_rows)


# ── REPL / effectful tool path ───────────────────────────────────────────────


def test_repl_effectful_gateway_decision_writes_one_receipt(workspace: Path) -> None:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(FileWriteTool(workspace_root=workspace))
    planner = FakePlanner(
        [
            {
                "tool": "file_write",
                "arguments": {"path": "g4.txt", "content": "nope"},
                "label": "file_write:g4",
                "expected_outcome": "writes file",
            }
        ]
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=["[synthesised]"]),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
        gateway_dry_run=True,
    )
    agent.run("write g4.txt")

    rows = _gateway_receipts(workspace)
    assert len(rows) == 1
    assert rows[0].operation == "gateway.simulate"
    assert rows[0].refs.get("tool_name") == "file_write"
    # Behavior preserved: dry-run simulate did not create the file.
    assert not (workspace / "g4.txt").exists()
