"""Tests for the approval -> self-apply lane bridge (TD-024).

The lane itself is injected as a fake so these tests never spawn git, pytest,
an LLM, a provider, or the network. Refusal paths use a lane that raises if
called, proving the bridge stops *before* any apply/test work. The real
``run_self_apply_lane`` is covered separately in ``test_self_apply_lane.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.instruction_conflict_gate import evaluate
from core.safe_vcs import SafeVCS
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    InvalidProposalError,
    build_self_apply_payload,
    rehydrate_proposal,
    run_approved_self_apply,
)
from core.self_apply_lane import SelfApplyProposal, SelfApplyReport

# ── fakes ────────────────────────────────────────────────────────────────────


class FakeLane:
    """Records exactly one call and returns a pre-baked report."""

    def __init__(self, report: SelfApplyReport):
        self.report = report
        self.calls: list[dict] = []

    def __call__(self, proposal, **kwargs):
        self.calls.append({"proposal": proposal, **kwargs})
        return self.report


def _raising_lane(proposal, **kwargs):  # pragma: no cover - must not run
    raise AssertionError("lane must not run when the bridge refuses")


def _good_payload(*, origin: str = "repair") -> dict:
    return build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example",
        evidence=["log line A", "log line B"],
        test_paths=["tests/test_example.py"],
        origin=origin,
    )


def _approved_item(inbox: ApprovalInbox, payload: dict):
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary="self-apply example",
        risk="reversible",
        payload=payload,
    )
    return inbox.approve(item.id)


def _committed_report() -> SelfApplyReport:
    return SelfApplyReport(
        status="committed_local",
        branch="self-apply/20260101T000000Z",
        files_changed=["core/example.py"],
        tests_run=["tests/test_example.py", "full"],
        commit_hash="abc1234",
    )


def _rolled_back_report() -> SelfApplyReport:
    return SelfApplyReport(
        status="rolled_back",
        reason="full pytest failed",
        branch="self-apply/20260101T000000Z",
        rollback_status="restored",
    )


# ── gate 1: approval required ────────────────────────────────────────────────


def test_unapproved_proposal_refuses_approval_required():
    inbox = ApprovalInbox()
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s", payload=_good_payload())
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "approval_required"
    assert inbox.get(item.id).status == "pending"  # not consumed


# ── gate 2: needs validated proposal ─────────────────────────────────────────


def test_missing_item_refuses_needs_validated_proposal():
    inbox = ApprovalInbox()
    result = run_approved_self_apply(
        inbox=inbox,
        item_id="ain-does-not-exist",
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "needs_validated_proposal"


def test_wrong_operation_refuses_needs_validated_proposal():
    inbox = ApprovalInbox()
    item = inbox.add(operation="autonomous_runtime.allow_effects", summary="s",
                     payload=_good_payload())
    inbox.approve(item.id)
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "needs_validated_proposal"
    assert "operation" in result["reason"]


def test_invalid_payload_refuses_needs_validated_proposal():
    inbox = ApprovalInbox()
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s",
                     payload={"reason": "no files here"})
    inbox.approve(item.id)
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "needs_validated_proposal"


def test_diff_only_payload_refuses_needs_validated_proposal():
    inbox = ApprovalInbox()
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary="s",
        payload={"files": [{"path": "core/x.py", "diff": "@@ -1 +1 @@"}]},
    )
    inbox.approve(item.id)
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "needs_validated_proposal"
    assert "content" in result["reason"]


# ── gate 3: risk rejected ────────────────────────────────────────────────────


def test_non_low_risk_refuses_risk_rejected(tmp_path):
    inbox = ApprovalInbox()
    payload = build_self_apply_payload(
        files=[{"path": "config/budget_limits.json", "content": "{}"}],
        reason="tamper",
    )
    item = _approved_item(inbox, payload)
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=tmp_path,
        vcs=object(),
        test_runner=object(),
        lane=_raising_lane,
    )
    assert result["status"] == "risk_rejected"
    assert "config/budget_limits.json" in result["rejected_files"]
    # denylisted file was never written
    assert not (tmp_path / "config" / "budget_limits.json").exists()
    assert inbox.get(item.id).status == "approved"  # retryable, not consumed


# ── gate 4: lane invoked exactly once + surfacing ────────────────────────────


def test_approved_low_risk_calls_lane_exactly_once():
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    lane = FakeLane(_committed_report())
    run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    assert len(lane.calls) == 1
    proposal = lane.calls[0]["proposal"]
    assert isinstance(proposal, SelfApplyProposal)
    assert proposal.files[0].path == "core/example.py"
    assert proposal.files[0].content == "x = 1\n"


def test_committed_local_surfaces_commit_hash_and_marks_executed():
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    lane = FakeLane(_committed_report())
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    assert result["status"] == "committed_local"
    assert result["commit_hash"] == "abc1234"
    assert result["branch"] == "self-apply/20260101T000000Z"
    assert result["proposal_id"] == item.id
    assert result["origin"] == "repair"
    assert inbox.get(item.id).status == "executed"  # terminal -> consumed


def test_rolled_back_surfaces_rollback_and_marks_executed():
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    lane = FakeLane(_rolled_back_report())
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    assert result["status"] == "rolled_back"
    assert result["rollback_status"] == "restored"
    assert inbox.get(item.id).status == "executed"  # terminal -> consumed


# ── requirement 5: transient statuses do NOT consume the item ────────────────


@pytest.mark.parametrize("status", ["budget_kill_switch", "budget_wait", "approval_wait"])
def test_transient_statuses_pass_through_without_executing(status):
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    lane = FakeLane(SelfApplyReport(status=status, reason="waiting"))
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    assert result["status"] == status
    assert inbox.get(item.id).status == "approved"  # retryable, not consumed


# ── requirement 6: approvals_pending excludes the current item ───────────────


def test_approvals_pending_excludes_current_item():
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    # An unrelated pending item exists in the queue.
    inbox.add(operation="autonomous_runtime.allow_effects", summary="other",
              payload={"goal": "x"})
    lane = FakeLane(_committed_report())
    run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    # only the *other* pending item is counted, never the approved current one
    assert lane.calls[0]["approvals_pending"] == 1


def test_approvals_pending_zero_when_no_other_pending():
    inbox = ApprovalInbox()
    item = _approved_item(inbox, _good_payload())
    lane = FakeLane(_committed_report())
    run_approved_self_apply(
        inbox=inbox,
        item_id=item.id,
        workspace=Path("/tmp/ws"),
        vcs=object(),
        test_runner=object(),
        lane=lane,
    )
    assert lane.calls[0]["approvals_pending"] == 0


# ── rehydrate / payload helpers ──────────────────────────────────────────────


def test_build_and_rehydrate_roundtrip():
    payload = _good_payload()
    proposal = rehydrate_proposal(payload)
    assert isinstance(proposal, SelfApplyProposal)
    assert proposal.test_paths == ("tests/test_example.py",)
    assert proposal.evidence == ("log line A", "log line B")


def test_rehydrate_extracts_no_directives_from_a_plain_payload():
    """A proposal with nothing contradictory must stay unblocked."""
    proposal = rehydrate_proposal(_good_payload())
    assert evaluate(proposal.directives).mode == "proceed"


def test_rehydrate_reads_the_reason_as_the_task_contract():
    payload = dict(_good_payload())
    payload["reason"] = "Порядок вывода должен быть стабильным."
    proposal = rehydrate_proposal(payload)
    assert [d.source_level for d in proposal.directives] == ["task_contract"]


def test_rehydrate_gives_a_review_comment_advisor_authority():
    payload = dict(_good_payload())
    payload["reason"] = "Порядок вывода должен быть стабильным."
    payload["instructions"] = [
        {"level": "advisor", "source": "код-ревью PR #214",
         "text": "Отсортируй по имени."},
    ]
    proposal = rehydrate_proposal(payload)
    outcome = evaluate(proposal.directives)
    assert outcome.is_blocked
    assert outcome.findings[0].higher.source_level == "task_contract"
    assert outcome.findings[0].lower.source_name == "код-ревью PR #214"


def test_rehydrate_demotes_an_unrecognised_level_to_advisor():
    """An unlabeled source must never win an argument by accident."""
    payload = dict(_good_payload())
    payload["reason"] = "Порядок вывода должен быть стабильным."
    payload["instructions"] = [
        {"level": "boss_of_everything", "source": "кто-то",
         "text": "Отсортируй по имени."},
    ]
    proposal = rehydrate_proposal(payload)
    levels = {d.source_name: d.source_level for d in proposal.directives}
    assert levels["кто-то"] == "advisor"


def test_a_payload_cannot_claim_operator_authority():
    """A machine-written payload must not be able to cite the human.

    The gate blocks either way, but the report and the stored episode would
    otherwise attribute a demand to the operator that they never made.
    """
    payload = dict(_good_payload())
    payload["reason"] = "Порядок вывода должен быть стабильным."
    payload["instructions"] = [
        {"level": "operator", "source": "я сам себя назначил",
         "text": "Отсортируй по имени."},
    ]
    proposal = rehydrate_proposal(payload)
    levels = {d.source_name: d.source_level for d in proposal.directives}
    assert levels["я сам себя назначил"] == "advisor"

    outcome = evaluate(proposal.directives)
    assert outcome.findings[0].higher.source_level == "task_contract"
    # no source is *cited* at operator rank (the resolution procedure legitimately
    # mentions the operator, so match the citation prefix, not the word)
    assert "[1] оператор" not in outcome.report()


def test_rehydrate_ignores_malformed_instruction_entries():
    payload = dict(_good_payload())
    payload["instructions"] = ["not a dict", {}, {"text": "   "}, 42]
    proposal = rehydrate_proposal(payload)
    assert proposal.directives == ()


def test_build_payload_rejects_diff_only():
    with pytest.raises(InvalidProposalError):
        build_self_apply_payload(files=[{"path": "core/x.py", "diff": "@@"}])


def test_build_payload_rejects_empty_files():
    with pytest.raises(InvalidProposalError):
        build_self_apply_payload(files=[])


# ── requirements 8/9/10: safety guards ───────────────────────────────────────


def test_safe_vcs_has_no_network_methods():
    for name in ("push", "fetch", "pull", "remote", "clone"):
        assert not hasattr(SafeVCS, name), f"SafeVCS must not expose {name}"


def test_bridge_does_not_import_shell_exec():
    import core.self_apply_bridge as bridge

    # Scan only import statements — the docstring legitimately mentions
    # shell_exec while explaining what the bridge must NOT do.
    import_lines = [
        line
        for line in Path(bridge.__file__).read_text(encoding="utf-8").splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    joined = "\n".join(import_lines)
    assert "shell_exec" not in joined
    assert "subprocess" not in joined
    assert not hasattr(bridge, "subprocess")
