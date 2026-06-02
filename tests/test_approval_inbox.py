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


# ==========================================================================
# Fix #4 — approval inbox TTL enforcement (expire_stale + default TTL)
# ==========================================================================

from datetime import datetime, timedelta, timezone


def _past_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _future_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class TestApprovalInboxExpiry:
    """expire_stale() aborts overdue pending items; pending() enforces TTL."""

    def test_expire_stale_aborts_overdue_item(self):
        inbox = ApprovalInbox()
        item = inbox.add(
            operation="op",
            summary="s",
            expires_at=_past_iso(2),
        )
        assert item.status == "pending"

        expired = inbox.expire_stale()

        assert expired == 1
        assert inbox.items[0].status == "aborted"

    def test_expire_stale_leaves_future_item_pending(self):
        inbox = ApprovalInbox()
        inbox.add(operation="op", summary="s", expires_at=_future_iso(24))

        expired = inbox.expire_stale()

        assert expired == 0
        assert inbox.items[0].status == "pending"

    def test_pending_filters_out_expired_items(self):
        inbox = ApprovalInbox()
        inbox.add(operation="past", summary="gone", expires_at=_past_iso(1))
        inbox.add(operation="future", summary="ok", expires_at=_future_iso(24))

        pending = inbox.pending()

        assert len(pending) == 1
        assert pending[0].operation == "future"

    def test_expire_stale_does_not_touch_non_pending_items(self):
        inbox = ApprovalInbox()
        item = inbox.add(
            operation="already-approved",
            summary="s",
            expires_at=_past_iso(2),
        )
        inbox.approve(item.id)

        expired = inbox.expire_stale()

        assert expired == 0
        assert inbox.items[0].status == "approved"

    def test_add_sets_default_ttl_when_expires_at_not_provided(self):
        inbox = ApprovalInbox()
        item = inbox.add(operation="default-ttl", summary="s")

        assert item.expires_at is not None
        exp = datetime.fromisoformat(item.expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        # Should be ~24 h from now — verify it is in the future
        assert exp > datetime.now(timezone.utc)

    def test_add_respects_explicit_expires_at(self):
        custom = _future_iso(48)
        inbox = ApprovalInbox()
        item = inbox.add(operation="custom", summary="s", expires_at=custom)
        assert item.expires_at == custom

    def test_expire_stale_returns_zero_on_empty_inbox(self):
        assert ApprovalInbox().expire_stale() == 0

    def test_multiple_expired_all_aborted(self):
        inbox = ApprovalInbox()
        for i in range(3):
            inbox.add(
                operation=f"op{i}", summary="s", expires_at=_past_iso(i + 1)
            )

        expired = inbox.expire_stale()

        assert expired == 3
        assert all(it.status == "aborted" for it in inbox.items)
