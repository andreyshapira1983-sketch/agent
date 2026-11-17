"""A verdict is final: the approval inbox moves only along its state machine."""
from __future__ import annotations

import pytest

from core.approval_inbox import ApprovalInbox, ApprovalTransitionError


def _inbox(tmp_path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")


def _item(inbox: ApprovalInbox):
    return inbox.add(operation="op.test", summary="do the thing")


def test_a_denied_request_cannot_be_approved(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    inbox.deny(item.id, reason="no", actor="operator")
    with pytest.raises(ApprovalTransitionError):
        inbox.approve(item.id, actor="someone")
    with pytest.raises(ApprovalTransitionError):
        inbox.set_status(item.id, "approved")
    kept = inbox.get(item.id)
    assert kept.status == "denied" and kept.decided_by == "operator"


def test_an_executed_request_cannot_be_approved_again(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    inbox.approve(item.id)
    inbox.mark_executed(item.id)
    for move in ("approved", "pending", "denied", "aborted", "executed"):
        with pytest.raises(ApprovalTransitionError):
            inbox.set_status(item.id, move)
    assert inbox.get(item.id).status == "executed"


def test_an_aborted_request_stays_aborted(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    inbox.abort(item.id)
    with pytest.raises(ApprovalTransitionError):
        inbox.approve(item.id)
    with pytest.raises(ApprovalTransitionError):
        inbox.mark_executed(item.id)
    assert inbox.get(item.id).status == "aborted"


def test_execution_needs_an_approval_first(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    with pytest.raises(ApprovalTransitionError):
        inbox.mark_executed(item.id)
    assert inbox.get(item.id).status == "pending"


def test_an_approval_cannot_be_re_signed_or_reopened(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    signed = inbox.approve(item.id, reason="first", actor="operator")
    # A repeated approval is a no-op: the first signature stands.
    assert inbox.approve(item.id, reason="second", actor="someone else") == signed
    with pytest.raises(ApprovalTransitionError):
        inbox.set_status(item.id, "pending")
    with pytest.raises(ApprovalTransitionError):
        inbox.deny(item.id, reason="changed my mind")
    assert inbox.get(item.id) == signed
    outcomes = (tmp_path / "data" / "approval_outcomes.jsonl").read_text(encoding="utf-8")
    assert len(outcomes.splitlines()) == 1


def test_a_refused_move_leaves_the_file_and_the_outcomes_untouched(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    item = _item(inbox)
    inbox.deny(item.id, reason="no")
    outcomes = tmp_path / "data" / "approval_outcomes.jsonl"
    before = (inbox.path.read_text(encoding="utf-8"), outcomes.read_text(encoding="utf-8"))
    with pytest.raises(ApprovalTransitionError):
        inbox.approve(item.id)
    assert (inbox.path.read_text(encoding="utf-8"), outcomes.read_text(encoding="utf-8")) == before


def test_the_legal_paths_still_work(tmp_path) -> None:
    inbox = _inbox(tmp_path)
    a, b, c, d = (_item(inbox) for _ in range(4))
    assert inbox.approve(a.id).status == "approved"
    assert inbox.mark_executed(a.id).status == "executed"
    assert inbox.deny(b.id, reason="no").status == "denied"
    assert inbox.abort(c.id).status == "aborted"
    inbox.approve(d.id)
    assert inbox.abort(d.id).status == "aborted"


def test_an_unknown_id_is_still_a_key_error(tmp_path) -> None:
    with pytest.raises(KeyError):
        _inbox(tmp_path).set_status("ain_missing", "approved")
