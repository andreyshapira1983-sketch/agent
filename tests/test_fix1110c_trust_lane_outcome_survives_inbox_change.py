"""The lane outcome is returned and recorded even when the inbox item changed while the lane ran."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
    run_approved_self_apply,
)
from core.self_apply_lane import SelfApplyReport
from core.self_build_producer import PRODUCER_ORIGIN


class _Registry:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []

    def apply_lane_outcome(self, item_id: str, status: str) -> None:
        self.outcomes.append((item_id, status))


def _approved(inbox: ApprovalInbox):
    payload = build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example",
        evidence=["log line A", "log line B"],
        test_paths=["tests/test_example.py"],
        origin=PRODUCER_ORIGIN,
    )
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="example", payload=payload)
    return inbox.approve(item.id)


def _run(inbox: ApprovalInbox, item_id: str, meanwhile, registry: _Registry) -> dict:
    def lane(proposal, **kwargs):
        meanwhile()
        return SelfApplyReport(
            status="committed_local", branch="self-apply/x", commit_hash="abc1234",
            files_changed=["core/example.py"],
        )

    return run_approved_self_apply(
        inbox=inbox, item_id=item_id, workspace=Path("/tmp/ws"),
        vcs=object(), test_runner=object(), lane=lane, registry=registry,
    )


def test_an_item_aborted_while_the_lane_ran_still_reports_the_commit() -> None:
    inbox = ApprovalInbox()
    item = _approved(inbox)
    registry = _Registry()

    result = _run(inbox, item.id, lambda: inbox.abort(item.id), registry)

    assert result["status"] == "committed_local" and result["commit_hash"] == "abc1234"
    assert registry.outcomes == [(item.id, "committed_local")]
    assert inbox.get(item.id).status == "aborted"
    notes = [r for r in result["risks"] if r.startswith("approval_not_consumed")]
    assert len(notes) == 1 and "committed_local" in notes[0] and "aborted" in notes[0]


def test_an_item_that_vanished_while_the_lane_ran_still_reports_the_commit() -> None:
    inbox = ApprovalInbox()
    item = _approved(inbox)
    registry = _Registry()

    result = _run(inbox, item.id, lambda: setattr(inbox, "items", []), registry)

    assert result["status"] == "committed_local" and result["commit_hash"] == "abc1234"
    assert registry.outcomes == [(item.id, "committed_local")]
    assert any("approval_not_consumed" in r and "not found" in r for r in result["risks"])


def test_a_clean_run_carries_no_such_note() -> None:
    inbox = ApprovalInbox()
    item = _approved(inbox)

    result = _run(inbox, item.id, lambda: None, _Registry())

    assert inbox.get(item.id).status == "executed"
    assert not [r for r in result.get("risks") or () if "approval_not_consumed" in r]


@pytest.mark.parametrize("exc_type", [RuntimeError, OSError])
def test_an_unrelated_inbox_failure_is_not_swallowed(exc_type) -> None:
    inbox = ApprovalInbox()
    item = _approved(inbox)

    def broken(item_id: str):
        raise exc_type("disk")

    inbox.mark_executed = broken  # type: ignore[method-assign]
    with pytest.raises(exc_type):
        _run(inbox, item.id, lambda: None, _Registry())
