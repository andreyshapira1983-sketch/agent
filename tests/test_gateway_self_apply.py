"""Gateway slice G3 — route the trusted self-apply lane through ActuationGateway.

The lane is faked so these tests never touch git/pytest/network. They prove the
gateway is consulted *before* any mutation, and that only an ``allow`` outcome
lets the lane run. Policy/approval/risk gates are covered in
``test_self_apply_approval_bridge.py`` and remain defense-in-depth.
"""
from __future__ import annotations

from pathlib import Path

from core.actuation_gateway import ActuationGateway, GatewayDecision
from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
    run_approved_self_apply,
)
from core.self_apply_lane import SelfApplyReport


class _RecordingLane:
    def __init__(self, report: SelfApplyReport):
        self.report = report
        self.calls: list[dict] = []

    def __call__(self, proposal, **kwargs):
        self.calls.append({"proposal": proposal, **kwargs})
        return self.report


class _FakeGateway:
    """Returns a fixed outcome and records that it was consulted."""

    def __init__(self, outcome: str):
        self.outcome = outcome
        self.calls = 0

    def evaluate_self_apply(self, *, operation: str = SELF_APPLY_OPERATION) -> GatewayDecision:
        self.calls += 1
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
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s", risk="reversible", payload=_payload())
    return inbox.approve(item.id).id


def _committed() -> SelfApplyReport:
    return SelfApplyReport(
        status="committed_local",
        branch="self-apply/x",
        files_changed=["core/example.py"],
        commit_hash="abc1234",
    )


def _run(inbox, item_id, lane, **kwargs):
    return run_approved_self_apply(
        inbox=inbox,
        item_id=item_id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
        **kwargs,
    )


def test_allow_lets_lane_run_after_gateway() -> None:
    inbox = ApprovalInbox()
    item_id = _approved(inbox)
    lane = _RecordingLane(_committed())
    gw = _FakeGateway("allow")

    result = _run(inbox, item_id, lane, gateway=gw)

    assert gw.calls == 1  # gateway consulted
    assert len(lane.calls) == 1  # lane ran exactly once
    assert result["status"] == "committed_local"
    assert inbox.get(item_id).status == "executed"


def test_dry_run_simulates_without_mutation() -> None:
    inbox = ApprovalInbox()
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run on gateway simulate")

    result = _run(inbox, item_id, _raising_lane, dry_run=True)

    assert result["status"] == "simulated"
    assert inbox.get(item_id).status == "approved"  # not consumed


def test_default_gateway_dry_run_uses_real_actuation_gateway() -> None:
    # No injected gateway: bridge builds ActuationGateway(path=self_apply, dry_run).
    inbox = ApprovalInbox()
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run on gateway simulate")

    result = _run(inbox, item_id, _raising_lane, dry_run=True)
    assert result["status"] == "simulated"


def test_deny_block_escalate_do_not_mutate() -> None:
    for outcome in ("deny", "block", "escalate"):
        inbox = ApprovalInbox()
        item_id = _approved(inbox)

        def _raising_lane(proposal, **kwargs):
            raise AssertionError(f"lane must not run on gateway {outcome}")

        result = _run(inbox, item_id, _raising_lane, gateway=_FakeGateway(outcome))
        assert result["status"] == "gateway_blocked"
        assert inbox.get(item_id).status == "approved"


def test_gateway_exception_fails_closed() -> None:
    inbox = ApprovalInbox()
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run when gateway raises")

    result = _run(inbox, item_id, _raising_lane, gateway=_BoomGateway())
    assert result["status"] == "gateway_error"
    assert inbox.get(item_id).status == "approved"


def test_default_allow_preserves_existing_behavior() -> None:
    # No gateway, no dry_run -> default gateway allows -> lane runs as before.
    inbox = ApprovalInbox()
    item_id = _approved(inbox)
    lane = _RecordingLane(_committed())

    result = _run(inbox, item_id, lane)

    assert len(lane.calls) == 1
    assert result["status"] == "committed_local"
    assert inbox.get(item_id).status == "executed"


def test_real_gateway_allow_when_not_dry_run() -> None:
    inbox = ApprovalInbox()
    item_id = _approved(inbox)
    lane = _RecordingLane(_committed())
    gw = ActuationGateway(policy=None, path="self_apply", dry_run=False)

    result = _run(inbox, item_id, lane, gateway=gw)

    assert len(lane.calls) == 1
    assert result["status"] == "committed_local"


class _ActiveKillSwitch:
    def status(self, snapshot=None, *, now_iso=None):
        from core.budget_kill_switch import KillSwitchState

        return KillSwitchState(
            active=True,
            reason="budget_kill_switch",
            counter="llm_calls",
            window="day",
            used=100,
            limit=100,
        )


def test_real_gateway_block_on_kill_switch() -> None:
    inbox = ApprovalInbox()
    item_id = _approved(inbox)

    def _raising_lane(proposal, **kwargs):
        raise AssertionError("lane must not run on gateway block")

    gw = ActuationGateway(
        policy=None,
        path="self_apply",
        dry_run=False,
        kill_switch=_ActiveKillSwitch(),
    )
    result = _run(inbox, item_id, _raising_lane, gateway=gw)

    assert result["status"] == "gateway_blocked"
    assert inbox.get(item_id).status == "approved"
