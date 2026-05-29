from __future__ import annotations

from core.approval_inbox import ApprovalInbox


def test_approval_inbox_adds_pending_item():
    inbox = ApprovalInbox()

    item = inbox.add(
        operation="autonomous_runtime.allow_effects",
        summary="Allow non-dry-run autonomous mode",
        risk="irreversible",
        reasons=("needs human review",),
    )

    assert item.status == "pending"
    assert item.risk == "irreversible"
    assert inbox.pending() == [item]
    assert inbox.snapshot()["pending"] == 1


def test_approval_inbox_snapshot_is_serialisable_shape():
    inbox = ApprovalInbox()
    inbox.add(operation="x", summary="y")

    snap = inbox.snapshot()

    assert snap["total"] == 1
    assert snap["items"][0]["operation"] == "x"
    assert snap["items"][0]["summary"] == "y"


def test_approval_inbox_persists_pending_items(workspace):
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    item = inbox.add(
        operation="autonomous_runtime.allow_effects",
        summary="Allow non-dry-run autonomous mode",
        risk="irreversible",
        payload={"goal": "project health", "include_tests": False},
    )

    reloaded = ApprovalInbox(path=path)

    assert reloaded.pending()[0].id == item.id
    assert reloaded.pending()[0].payload["goal"] == "project health"
    assert reloaded.snapshot()["pending"] == 1


def test_approval_inbox_approve_and_deny_persist_status(workspace):
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    approved = inbox.add(operation="approve-me", summary="Approve me")
    denied = inbox.add(operation="deny-me", summary="Deny me")

    assert inbox.approve(approved.id).status == "approved"
    assert inbox.deny(denied.id).status == "denied"

    reloaded = ApprovalInbox(path=path)
    assert reloaded.list(status="approved")[0].id == approved.id
    assert reloaded.list(status="denied")[0].id == denied.id
    assert reloaded.snapshot()["pending"] == 0


def test_approval_inbox_abort_and_executed_statuses(workspace):
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    aborted = inbox.add(operation="abort-me", summary="Abort me")
    executed = inbox.add(operation="execute-me", summary="Execute me")

    assert inbox.abort(aborted.id).status == "aborted"
    assert inbox.mark_executed(executed.id).status == "executed"

    reloaded = ApprovalInbox(path=path)
    assert reloaded.list(status="aborted")[0].id == aborted.id
    assert reloaded.list(status="executed")[0].id == executed.id
