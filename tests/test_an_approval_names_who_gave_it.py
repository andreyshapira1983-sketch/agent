"""An approval must name who gave it — the sovereignty axis of the provenance gap.

Measured 2026-08-22 across every durable surface: reviewer identity is absent
from all three. `ApprovalInboxItem` has no reviewer field; `approve()` and
`deny()` take a `reason` and no actor; the separate review channel written by
`_record_outcome` carries ts / id / operation / summary / verdict / reason and
no actor; the approval receipt carries trace, path, fingerprints and no actor.
`requested_by` is a constant component name in 137 of 137 live rows.

So the record can say a request was made and that a verdict happened. It cannot
say WHO gave the verdict — and §9 places approval of irreversible actions with
the human. A record that cannot name the approver cannot support that claim
after the fact, which is precisely when it would be needed.

WHAT THIS RECORDS. An `actor` on the verdict, and the honest default when none
is supplied: `unattributed`. Not "operator" — assuming the human when nobody
said so is how a record starts lying comfortably. An automatic approver names
itself, because a policy approving on a rule is a different fact from a person
approving on judgement, and merging them is exactly the collapse the operator's
ratified norm B forbids.

WHAT THIS DOES NOT DO. It does not authenticate anyone: a caller that claims to
be the operator is believed. This is a RECORD of who claimed the verdict, not
proof of identity — the difference is stated here so a later reader does not
mistake the field for authentication it never performed.
"""
from __future__ import annotations

import json

import pytest

from core.approval_inbox import ApprovalInbox, ApprovalInboxItem


@pytest.fixture()
def inbox(workspace):
    box = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    box.add(operation="autonomous_runtime.allow_effects",
            summary="probe", risk="irreversible")
    return box


def _only(inbox: ApprovalInbox) -> ApprovalInboxItem:
    return inbox.items[0]


def test_an_approval_carries_the_actor_who_gave_it(inbox) -> None:
    item = inbox.approve(_only(inbox).id, reason="ok", actor="operator")
    assert item.decided_by == "operator"


def test_a_denial_carries_it_too(inbox) -> None:
    """A refusal is a verdict like any other, and knowing who refused matters
    as much as knowing who allowed."""
    item = inbox.deny(_only(inbox).id, reason="not now", actor="operator")
    assert item.decided_by == "operator"


def test_an_unnamed_verdict_is_unattributed_not_assumed_to_be_the_human() -> None:
    """The honest default. Filling in "operator" when nobody said so is how a
    record starts lying comfortably — and this is the one field whose whole
    purpose is to support a claim about the human."""
    from core.approval_inbox import ApprovalInbox as Box
    box = Box(path=None)
    box.add(operation="probe", summary="s", risk="reversible")
    item = box.approve(box.items[0].id, reason="")
    assert item.decided_by == "unattributed", (
        f"an unnamed approval recorded {item.decided_by!r} — a verdict nobody "
        "signed must not be attributed to the human by default"
    )


def test_the_actor_survives_the_round_trip_to_disk(inbox, workspace) -> None:
    """A distinction that never leaves the process cannot be audited after the
    run, which is the entire reason these axes were called a gap."""
    inbox.approve(_only(inbox).id, reason="ok", actor="operator")
    reloaded = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    assert reloaded.items[0].decided_by == "operator"


def test_the_review_channel_records_the_actor(inbox, workspace) -> None:
    """`_record_outcome` writes the verdict history separately from the
    lifecycle status. It was the one surface that recorded a verdict at all,
    and it recorded it anonymously."""
    inbox.approve(_only(inbox).id, reason="ok", actor="operator")
    path = workspace / "data" / "approval_outcomes.jsonl"
    if not path.exists():
        pytest.skip("no receipt workspace bound in this fixture")
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    payloads = [r.get("payload", r) for r in rows]
    assert payloads, "the review channel recorded nothing"
    assert payloads[-1].get("decided_by") == "operator", (
        "the verdict history still cannot name who gave the verdict"
    )


def test_the_live_decision_object_already_distinguishes_its_responder() -> None:
    """Measured while building this, and it corrects the premise: the AXIS IS
    NOT UNIFORMLY EMPTY. `ApprovalDecision` has carried `responder`
    (user / auto / timeout) all along, so the in-process verdict object could
    always say whether a human or a policy answered.

    What was missing is the DURABLE side: the inbox item and its outcome row —
    the 137 live rows — recorded no actor at all. So this test pins the half
    that already worked, rather than adding a second field beside it and
    creating two sources of one truth."""
    from core.approval import AutoApprover, CLIApprovalProvider
    from core.models import ApprovalRequest

    req = ApprovalRequest(action_id="a", step_id="s", tool_name="file_write",
                          risk="reversible", arguments={"path": "a.md"})
    assert AutoApprover().request(req).responder == "auto"

    import io
    human = CLIApprovalProvider(input_fn=lambda _p: "y", out=io.StringIO())
    assert human.request(req).responder == "user"


def test_the_field_is_a_record_not_authentication(inbox) -> None:
    """Stated as a test so nobody later mistakes the field for proof: a caller
    that claims to be the operator is believed. This records WHO CLAIMED the
    verdict."""
    item = inbox.approve(_only(inbox).id, reason="", actor="someone_else")
    assert item.decided_by == "someone_else"
