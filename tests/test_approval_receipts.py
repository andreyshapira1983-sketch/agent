"""Tool receipts slice 1b-a — approval inbox transition receipts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.tool_receipts as tool_receipts
from core.approval_inbox import ApprovalInbox
from core.tool_receipts import ToolReceiptLedger, default_receipts_path, record_approval_receipt


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")


def _receipts(workspace: Path) -> list:
    return ToolReceiptLedger(default_receipts_path(workspace)).load()


def test_add_writes_one_approval_receipt(workspace: Path) -> None:
    inbox = _inbox(workspace)
    item = inbox.add(operation="capability_request", summary="need X", risk="external")

    receipts = _receipts(workspace)
    add_rows = [r for r in receipts if r.operation == "approval_inbox.add"]
    assert len(add_rows) == 1
    row = add_rows[0]
    assert row.kind == "approval"
    assert row.status == "success"
    assert row.risk == "external"
    assert row.refs.get("approval_id") == item.id


def test_duplicate_add_with_dedup_key_writes_no_second_receipt(workspace: Path) -> None:
    inbox = _inbox(workspace)
    first = inbox.add(operation="proposed_task", summary="do thing", dedup_key="k1")
    second = inbox.add(operation="proposed_task", summary="do thing", dedup_key="k1")

    assert first.id == second.id  # dedup returned the existing item
    add_rows = [r for r in _receipts(workspace) if r.operation == "approval_inbox.add"]
    assert len(add_rows) == 1


def test_transitions_write_receipts(workspace: Path) -> None:
    inbox = _inbox(workspace)
    a = inbox.add(operation="op.a", summary="a")
    b = inbox.add(operation="op.b", summary="b")
    c = inbox.add(operation="op.c", summary="c")
    d = inbox.add(operation="op.d", summary="d")

    inbox.approve(a.id)
    inbox.mark_executed(a.id)
    inbox.deny(b.id)
    inbox.abort(c.id)
    inbox.set_status(d.id, "approved")

    ops = [r.operation for r in _receipts(workspace)]
    assert ops.count("approval_inbox.add") == 4
    assert "approval_inbox.approve" in ops
    assert "approval_inbox.mark_executed" in ops
    assert "approval_inbox.deny" in ops
    assert "approval_inbox.abort" in ops

    # refs point back at the right item and status is captured via fingerprint.
    approve_rows = [r for r in _receipts(workspace) if r.operation == "approval_inbox.approve"]
    assert any(r.refs.get("approval_id") == a.id for r in approve_rows)


def test_invalid_id_writes_no_success_receipt(workspace: Path) -> None:
    inbox = _inbox(workspace)
    before = len(_receipts(workspace))
    with pytest.raises(KeyError):
        inbox.approve("ain_does_not_exist")
    after = _receipts(workspace)
    # No transition receipt for a failed lookup.
    assert len(after) == before
    assert not any(r.operation == "approval_inbox.approve" for r in after)


def test_receipts_contain_no_raw_secret(workspace: Path) -> None:
    secret = "sk-SECRET1234567890ABCDEF"
    inbox = _inbox(workspace)
    item = inbox.add(
        operation="capability_request",
        summary=f"needs token {secret}",
        reasons=(f"key={secret}",),
        payload={"api_key": secret},
    )
    inbox.approve(item.id)

    ledger_path = default_receipts_path(workspace)
    raw = ledger_path.read_text(encoding="utf-8")
    assert secret not in raw
    for r in _receipts(workspace):
        assert secret not in json.dumps(r.to_dict(), ensure_ascii=False)


def test_record_approval_receipt_never_raises_on_append_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented contract: record_approval_receipt returns None, never raises,
    even when the underlying ledger append fails."""

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tool_receipts, "append_receipt", _boom)

    item = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl").add(
        operation="op", summary="s"
    )
    # add() already swallows via _emit_receipt; call the writer directly too.
    result = record_approval_receipt(
        "approval_inbox.add", item, workspace=workspace
    )
    assert result is None


def test_in_memory_inbox_writes_no_receipt(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No path -> no workspace -> no receipt ledger side effect.
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    inbox = ApprovalInbox()
    item = inbox.add(operation="op", summary="s")
    inbox.approve(item.id)
    assert not default_receipts_path(workspace).exists()
