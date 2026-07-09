"""TD-031 — lane/approval outcomes feeding the subagent ledger.

All tests are hermetic: no git, pytest subprocess, LLM, provider, or network.
The self-apply lane is injected as a fake; the registry writes to a tmp path.
"""
from __future__ import annotations

from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import (
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
    run_approved_self_apply,
)
from core.self_apply_lane import SelfApplyReport
from core.self_build_producer import PRODUCER_ORIGIN
from core.subagent_registry import LANE_OUTCOME_ROLE, SubagentRegistry
from cli.commands_approval import _record_producer_approval


# ── fakes ──────────────────────────────────────────────────────────────────────


class _FakeLane:
    def __init__(self, report: SelfApplyReport):
        self.report = report
        self.calls = 0

    def __call__(self, proposal, **kwargs):
        self.calls += 1
        return self.report


class _BoomRegistry:
    """apply_lane_outcome always raises, to prove the hook is best-effort."""

    def apply_lane_outcome(self, *args, **kwargs):
        raise RuntimeError("boom")


def _producer_payload() -> dict:
    return build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example",
        evidence=["log A", "log B"],
        test_paths=["tests/test_example.py"],
        origin=PRODUCER_ORIGIN,
    )


def _foreign_payload() -> dict:
    return build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example",
        evidence=["log A", "log B"],
        test_paths=["tests/test_example.py"],
        origin="repair",  # not the producer
    )


def _approved(inbox: ApprovalInbox, payload: dict):
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s", payload=payload)
    return inbox.approve(item.id)


def _committed() -> SelfApplyReport:
    return SelfApplyReport(status="committed_local", commit_hash="abc1234")


def _rolled_back() -> SelfApplyReport:
    return SelfApplyReport(status="rolled_back", reason="tests failed",
                           rollback_status="restored")


def _run(inbox, item_id, workspace, lane, registry=None):
    return run_approved_self_apply(
        inbox=inbox,
        item_id=item_id,
        workspace=workspace,
        vcs=object(),
        test_runner=object(),
        lane=lane,
        registry=registry,
    )


# ── canonical attribution (registry unit) ──────────────────────────────────────


def test_canonical_role_mapping():
    assert LANE_OUTCOME_ROLE == {
        "approved": "reporter",
        "committed_local": "builder",
        "rolled_back": "builder",
    }


def test_apply_lane_outcome_credits_reporter_for_approved(tmp_path):
    reg = SubagentRegistry(tmp_path)
    assert reg.apply_lane_outcome("ain_1", "approved") is True
    assert reg.roles["reporter"].proposals_approved == 1
    # builder untouched
    assert reg.roles["builder"].committed_local == 0


def test_apply_lane_outcome_credits_builder_for_terminal(tmp_path):
    reg = SubagentRegistry(tmp_path)
    assert reg.apply_lane_outcome("ain_1", "committed_local") is True
    assert reg.apply_lane_outcome("ain_2", "rolled_back") is True
    assert reg.roles["builder"].committed_local == 1
    assert reg.roles["builder"].rolled_back == 1


def test_unknown_outcome_is_ignored(tmp_path):
    reg = SubagentRegistry(tmp_path)
    assert reg.apply_lane_outcome("ain_1", "budget_wait") is False
    assert reg.roles["builder"].committed_local == 0
    assert reg.applied_outcomes == set()


def test_dedup_prevents_double_count_and_persists(tmp_path):
    reg = SubagentRegistry(tmp_path)
    assert reg.apply_lane_outcome("ain_1", "committed_local") is True
    assert reg.apply_lane_outcome("ain_1", "committed_local") is False  # dup
    assert reg.roles["builder"].committed_local == 1
    # persisted dedup survives reload
    reloaded = SubagentRegistry.load(tmp_path)
    assert "ain_1:builder:committed_local" in reloaded.applied_outcomes
    assert reloaded.apply_lane_outcome("ain_1", "committed_local") is False
    assert reloaded.roles["builder"].committed_local == 1


def test_dedup_key_includes_role_and_outcome(tmp_path):
    # Same item, two different outcomes both record (distinct keys).
    reg = SubagentRegistry(tmp_path)
    assert reg.apply_lane_outcome("ain_1", "approved") is True
    assert reg.apply_lane_outcome("ain_1", "rolled_back") is True
    assert reg.roles["reporter"].proposals_approved == 1
    assert reg.roles["builder"].rolled_back == 1


def test_outcome_never_mutates_role_status(tmp_path):
    reg = SubagentRegistry(tmp_path)
    for i in range(10):
        reg.apply_lane_outcome(f"ain_{i}", "rolled_back")
    # advisory recommendation may move, but stored status must remain active
    assert reg.roles["builder"].status == "active"


def test_tolerant_load_of_old_registry_without_applied_outcomes(tmp_path):
    reg = SubagentRegistry(tmp_path)
    reg.save()
    import json
    raw = json.loads(reg.path.read_text(encoding="utf-8"))
    raw.pop("applied_outcomes", None)  # simulate a pre-TD-031 ledger
    reg.path.write_text(json.dumps(raw), encoding="utf-8")
    reloaded = SubagentRegistry.load(tmp_path)
    assert reloaded.applied_outcomes == set()
    assert reloaded.apply_lane_outcome("ain_1", "approved") is True


# ── bridge terminal hook ───────────────────────────────────────────────────────


def test_bridge_records_committed_for_producer_item(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    reg = SubagentRegistry(tmp_path)
    result = _run(inbox, item.id, tmp_path, _FakeLane(_committed()), registry=reg)
    assert result["status"] == "committed_local"
    reloaded = SubagentRegistry.load(tmp_path)
    assert reloaded.roles["builder"].committed_local == 1


def test_bridge_records_rolled_back_for_producer_item(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    reg = SubagentRegistry(tmp_path)
    _run(inbox, item.id, tmp_path, _FakeLane(_rolled_back()), registry=reg)
    assert SubagentRegistry.load(tmp_path).roles["builder"].rolled_back == 1


def test_bridge_ignores_non_producer_origin(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _foreign_payload())
    reg = SubagentRegistry(tmp_path)
    _run(inbox, item.id, tmp_path, _FakeLane(_committed()), registry=reg)
    assert reg.roles["builder"].committed_local == 0
    assert not reg.path.exists()  # nothing recorded -> no write


def test_bridge_none_registry_is_unchanged(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    result = _run(inbox, item.id, tmp_path, _FakeLane(_committed()), registry=None)
    assert result["status"] == "committed_local"
    # item still consumed exactly as before
    assert inbox.get(item.id).status == "executed"
    assert not (tmp_path / "data" / "subagent_registry.json").exists()


def test_bridge_non_terminal_status_records_nothing(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    reg = SubagentRegistry(tmp_path)
    report = SelfApplyReport(status="budget_wait", reason="kill-switch or low budget")
    result = _run(inbox, item.id, tmp_path, _FakeLane(report), registry=reg)
    assert result["status"] == "budget_wait"
    assert reg.roles["builder"].committed_local == 0
    assert inbox.get(item.id).status == "approved"  # not consumed


def test_bridge_registry_write_failure_never_breaks_flow(tmp_path):
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    result = _run(inbox, item.id, tmp_path, _FakeLane(_committed()), registry=_BoomRegistry())
    assert result["status"] == "committed_local"
    assert inbox.get(item.id).status == "executed"


def test_bridge_hook_is_idempotent_on_repeat(tmp_path):
    # Directly re-invoking the recording for the same terminal outcome dedups.
    inbox = ApprovalInbox()
    item = _approved(inbox, _producer_payload())
    reg = SubagentRegistry(tmp_path)
    from core.self_apply_bridge import _record_lane_outcome
    _record_lane_outcome(reg, item, "committed_local")
    _record_lane_outcome(reg, item, "committed_local")
    assert reg.roles["builder"].committed_local == 1


# ── technical success is not confirmed value (TD-031 amendment) ────────────────


def test_committed_local_counts_but_is_not_high_value(tmp_path):
    """A committed_local lane outcome is a *technical* success, not confirmed
    value: it must increment the counter without pushing Builder to high
    usefulness on its own. Evidence: the live redaction.py run committed a
    trivial comment edit that humans rejected as low value.
    """
    reg = SubagentRegistry(tmp_path)
    # Builder ran once and built (invocations=1). No producer-stage value yet.
    reg.record_report({"roles": [{"role": "builder", "decision": "built"}]},
                      save=False)
    builder = reg.roles["builder"]
    assert builder.invocations == 1
    assert builder.usefulness_score == 0.0

    # Downstream: the change reached committed_local.
    assert reg.apply_lane_outcome("ain_1", "committed_local", save=False) is True
    assert builder.committed_local == 1  # technical outcome recorded

    # ...but that alone must NOT mark Builder high-value.
    assert builder.usefulness_score < 0.5
    assert builder.usefulness_score < 1.0


def test_technical_success_is_weighted_below_producer_stage_value(tmp_path):
    """Same invocation count: a producer-stage value event (a Critic veto)
    outweighs a technical lane success (committed_local)."""
    reg = SubagentRegistry(tmp_path)
    # builder: 1 invocation + committed_local (technical only)
    reg.record_report({"roles": [{"role": "builder", "decision": "built"}]},
                      save=False)
    reg.apply_lane_outcome("ain_1", "committed_local", save=False)
    # critic: 1 invocation + a veto (producer-stage value, full weight)
    reg.record_report({"roles": [{"role": "critic", "decision": "veto"}]},
                      save=False)
    assert reg.roles["builder"].usefulness_score < reg.roles["critic"].usefulness_score


# ── approve hook (commands_approval) ────────────────────────────────────────────


def test_approve_hook_records_approved_for_producer_item(tmp_path):
    inbox = ApprovalInbox()
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s",
                     payload=_producer_payload())
    approved = inbox.approve(item.id)
    _record_producer_approval(tmp_path, approved)
    assert SubagentRegistry.load(tmp_path).roles["reporter"].proposals_approved == 1


def test_approve_hook_ignores_non_producer_origin(tmp_path):
    inbox = ApprovalInbox()
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s",
                     payload=_foreign_payload())
    approved = inbox.approve(item.id)
    _record_producer_approval(tmp_path, approved)
    assert not (tmp_path / "data" / "subagent_registry.json").exists()


def test_approve_hook_ignores_wrong_operation(tmp_path):
    inbox = ApprovalInbox()
    item = inbox.add(operation="something_else", summary="s",
                     payload={"origin": PRODUCER_ORIGIN})
    approved = inbox.approve(item.id)
    _record_producer_approval(tmp_path, approved)
    assert not (tmp_path / "data" / "subagent_registry.json").exists()
