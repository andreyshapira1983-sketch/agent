"""Gateway slice G4 — gateway decision receipts (kind="gateway")."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import tool_receipts
from core.actuation_gateway import GatewayDecision
from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
    run_approved_self_apply,
)
from core.tool_receipts import (
    ToolReceiptLedger,
    default_receipts_path,
    record_gateway_receipt,
)


def _gateway_receipts(ws: Path) -> list:
    return [
        r
        for r in ToolReceiptLedger(default_receipts_path(ws)).load()
        if r.kind == "gateway"
    ]


# ── unit: record_gateway_receipt ─────────────────────────────────────────────


def test_record_gateway_receipt_never_raises_on_append_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tool_receipts, "append_receipt", _boom)
    decision = GatewayDecision(outcome="allow", tool_name="file_write", path="repl")
    assert record_gateway_receipt(decision, workspace=workspace) is None


# ── self-apply path (G3 + G4) ────────────────────────────────────────────────


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


def test_self_apply_gateway_exception_fails_closed_with_an_error_receipt(
    workspace: Path,
) -> None:
    """A raising gateway refuses the apply and leaves one error receipt for this proposal.

    The receipt is the only durable record that the apply was attempted (MIR-077).
    """
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run when gateway raises")

    result = _run(inbox, item_id, workspace, _raising_lane, gateway=_BoomGateway())

    assert result["status"] == "gateway_error"
    assert inbox.get(item_id).status == "approved"
    rows = _gateway_receipts(workspace)
    assert [(r.status, r.operation, r.refs.get("proposal_id")) for r in rows] == [
        ("error", "gateway.deny", item_id)
    ]
