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
    )

    reloaded = ApprovalInbox(path=path)

    assert reloaded.pending()[0].id == item.id
    assert reloaded.snapshot()["pending"] == 1
