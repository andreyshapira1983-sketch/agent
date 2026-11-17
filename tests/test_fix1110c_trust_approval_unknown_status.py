"""An inbox row with an unknown status is kept in the file and is never a live request."""
from __future__ import annotations

import logging

import pytest

from core.approval_inbox import ApprovalInbox, ApprovalInboxItem
from core.state_integrity import append_state_jsonl, read_state_jsonl


def _row(item_id: str, status: object) -> dict:
    return {"id": item_id, "operation": "op.test", "summary": "odd row", "status": status}


def _inbox_with(tmp_path, *rows: dict) -> ApprovalInbox:
    path = tmp_path / "data" / "approval_inbox.jsonl"
    path.parent.mkdir(parents=True)
    append_state_jsonl(path, list(rows))
    return ApprovalInbox(path=path)


@pytest.mark.parametrize("status", [None, "", 0, False, [], "Approved", "done", " pending"])
def test_a_status_that_is_not_a_known_word_is_refused(status) -> None:
    with pytest.raises(ValueError, match="invalid approval status"):
        ApprovalInboxItem.from_dict(_row("ain_x", status))


def test_a_row_without_a_status_field_is_still_pending() -> None:
    assert ApprovalInboxItem.from_dict({"id": "ain_x", "operation": "op"}).status == "pending"


@pytest.mark.parametrize("status", [None, "", "granted"])
def test_an_odd_row_is_not_actionable_and_is_named_in_the_log(tmp_path, caplog, status) -> None:
    with caplog.at_level(logging.WARNING, logger="core.approval_inbox"):
        inbox = _inbox_with(tmp_path, _row("ain_odd", status))
        assert inbox.pending() == [] and inbox.list() == []
        assert inbox.get("ain_odd") is None
        with pytest.raises(KeyError, match="not found"):
            inbox.approve("ain_odd")
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1, lines  # once, not on every re-read
    assert "ain_odd" in lines[0] and "invalid approval status" in lines[0]


def test_an_odd_row_survives_every_later_write(tmp_path) -> None:
    odd = _row("ain_odd", "granted")
    inbox = _inbox_with(tmp_path, odd)
    item = inbox.add(operation="op.test", summary="a real request")
    inbox.approve(item.id)
    inbox.mark_executed(item.id)

    rows = {row["id"]: row for row in read_state_jsonl(inbox.path)}
    assert rows["ain_odd"] == odd
    assert rows[item.id]["status"] == "executed"
    assert ApprovalInbox(path=inbox.path).get(item.id).status == "executed"
