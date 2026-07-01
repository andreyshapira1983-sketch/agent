"""Tests for the subagent role performance ledger (TD-028).

The registry RECORDS and REPORTS role performance; it must never change a role's
status on its own (recommendations are advisory), never run an LLM/provider, and
never apply/commit/push. These tests pin the decision mapping (clarifications
4/5/6), the advisory-only recommendations, persistence + corrupt-file recovery,
and the additive producer hook that leaves producer behaviour unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.subagent_registry import (
    DEFAULT_ROLES,
    REGISTRY_PATH,
    RoleRecord,
    SubagentRegistry,
)


# ── report helpers (mirror ProducerReport.to_dict shape) ──────────────────────


def _report(status: str, roles: list[tuple[str, str]]) -> dict:
    return {
        "status": status,
        "roles": [{"role": r, "decision": d, "detail": "", "data": {}} for r, d in roles],
    }


def _full_proposed() -> dict:
    return _report(
        "proposed",
        [
            ("manager", "selected"),
            ("researcher", "gathered"),
            ("builder", "built"),
            ("critic", "pass"),
            ("reporter", "published"),
        ],
    )


# ── 1. defaults ───────────────────────────────────────────────────────────────


def test_new_registry_returns_default_active_roles(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    assert set(reg.roles) == set(DEFAULT_ROLES)
    for role_id in DEFAULT_ROLES:
        rec = reg.roles[role_id]
        assert rec.status == "active"
        assert rec.invocations == 0
        assert rec.recommendation == "keep"


# ── 2. recording updates counts for every role that ran ───────────────────────


def test_recording_full_run_updates_all_role_counts(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    assert reg.record_report(_full_proposed()) is True
    for role_id in DEFAULT_ROLES:
        assert reg.roles[role_id].invocations == 1
        assert reg.roles[role_id].successes == 1
    # A proposed run credits the reporter with one created proposal.
    assert reg.roles["reporter"].proposals_created == 1


def test_gate_wait_report_with_no_roles_is_a_noop(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    recorded = reg.record_report({"status": "budget_kill_switch", "roles": []})
    assert recorded is False
    assert all(reg.roles[r].invocations == 0 for r in DEFAULT_ROLES)


# ── 3. success raises trust / usefulness ──────────────────────────────────────


def test_success_increases_trust_and_usefulness(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    for _ in range(5):
        reg.record_report(_full_proposed())
    builder = reg.roles["builder"]
    assert builder.trust_score == 1.0
    reporter = reg.roles["reporter"]
    assert reporter.usefulness_score > 0.0  # created proposals => useful


# ── 4. failure / veto move recommendation, veto is NOT a critic failure ───────


def test_builder_failures_lower_recommendation(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    for _ in range(6):
        reg.record_report(_report("no_patch", [("builder", "failed")]))
    builder = reg.roles["builder"]
    assert builder.failures == 6
    assert builder.trust_score == 0.0
    assert builder.recommendation in ("pause", "retire")
    # Advisory only: status must remain active.
    assert builder.status == "active"


def test_critic_veto_is_useful_not_failure(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    # Six vetoed runs: builder built, critic vetoed.
    for _ in range(6):
        reg.record_report(
            _report("critic_veto", [("builder", "built"), ("critic", "veto")])
        )
    critic = reg.roles["critic"]
    assert critic.vetoes == 6
    assert critic.failures == 0
    assert critic.trust_score == 1.0  # vetoes count as positives
    assert critic.recommendation == "keep"  # a diligent critic is never retired


def test_repeated_vetoed_builder_outputs_move_builder_recommendation(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    for _ in range(6):
        reg.record_report(
            _report("critic_veto", [("builder", "built"), ("critic", "veto")])
        )
    builder = reg.roles["builder"]
    # builder "built" counts as success, but each vetoed output is attributed.
    assert builder.successes == 6
    assert builder.outputs_vetoed == 6
    assert builder.trust_score == 0.5
    # Still advisory only.
    assert builder.status == "active"


# ── 5. repeated failures can recommend retire but never enforce it ────────────


def test_retire_is_only_a_recommendation(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    for _ in range(8):
        reg.record_report(_report("no_patch", [("builder", "failed")]))
    builder = reg.roles["builder"]
    assert builder.recommendation == "retire"
    assert builder.status == "active"  # NOT auto-retired


def test_manager_no_target_is_neutral(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    for _ in range(6):
        reg.record_report(_report("no_patch", [("manager", "no_target")]))
    manager = reg.roles["manager"]
    assert manager.invocations == 6
    assert manager.successes == 0
    assert manager.failures == 0
    # No judged events -> keep, never punished for saying "nothing to do".
    assert manager.recommendation == "keep"


# ── 6. persistence round-trip ─────────────────────────────────────────────────


def test_ledger_persists_and_reloads(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    reg.record_report(_full_proposed())
    assert (tmp_path / REGISTRY_PATH).exists()

    reloaded = SubagentRegistry.load(tmp_path)
    assert reloaded.roles["reporter"].proposals_created == 1
    assert reloaded.roles["builder"].successes == 1


# ── 7. corrupt ledger degrades safely ─────────────────────────────────────────


def test_corrupt_ledger_degrades_to_defaults(tmp_path: Path):
    path = tmp_path / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    reg = SubagentRegistry.load(tmp_path)
    assert set(reg.roles) == set(DEFAULT_ROLES)
    assert reg.roles["manager"].invocations == 0


def test_role_record_from_dict_tolerates_garbage_fields():
    rec = RoleRecord.from_dict(
        {"role_id": "builder", "invocations": "oops", "status": "bogus"}
    )
    assert rec.role_id == "builder"
    assert rec.invocations == 0
    assert rec.status == "active"  # invalid status falls back


# ── 8. producer hook records without changing behaviour ───────────────────────


def test_producer_hook_records_role_outcomes(tmp_path: Path):
    from core.approval_inbox import ApprovalInbox
    from core.self_build_producer import produce_self_apply_proposal

    class _FakeLLM:
        # Manager selects no target -> no_patch (one role recorded, no item).
        def complete(self, *, system, user, max_tokens=2000, temperature=0.0):
            return json.dumps({"target": None, "diagnosis": "nothing"})

    reg = SubagentRegistry.load(tmp_path)
    inbox = ApprovalInbox(path=None)
    report = produce_self_apply_proposal(
        workspace=tmp_path,
        inbox=inbox,
        llm=_FakeLLM(),
        registry=reg,
    )
    assert report.status == "no_patch"
    assert reg.roles["manager"].invocations == 1
    # No item created, so reporter never ran.
    assert reg.roles["reporter"].invocations == 0


def test_producer_without_registry_is_unchanged(tmp_path: Path):
    from core.approval_inbox import ApprovalInbox
    from core.self_build_producer import produce_self_apply_proposal

    class _FakeLLM:
        def complete(self, *, system, user, max_tokens=2000, temperature=0.0):
            return json.dumps({"target": None, "diagnosis": "nothing"})

    inbox = ApprovalInbox(path=None)
    report = produce_self_apply_proposal(
        workspace=tmp_path, inbox=inbox, llm=_FakeLLM()
    )
    assert report.status == "no_patch"
    # Default None registry writes no ledger file at all.
    assert not (tmp_path / REGISTRY_PATH).exists()


def test_producer_hook_never_breaks_on_registry_write_failure(tmp_path: Path):
    from core.approval_inbox import ApprovalInbox
    from core.self_build_producer import produce_self_apply_proposal

    class _FakeLLM:
        def complete(self, *, system, user, max_tokens=2000, temperature=0.0):
            return json.dumps({"target": None, "diagnosis": "nothing"})

    class _BoomRegistry:
        def record_report(self, report):
            raise RuntimeError("disk full")

    inbox = ApprovalInbox(path=None)
    report = produce_self_apply_proposal(
        workspace=tmp_path,
        inbox=inbox,
        llm=_FakeLLM(),
        registry=_BoomRegistry(),
    )
    assert report.status == "no_patch"  # producer still returns normally


# ── 9-11. read-only report, no side effects ───────────────────────────────────


def test_status_report_is_read_only(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    reg.record_report(_full_proposed())
    before = (tmp_path / REGISTRY_PATH).read_text(encoding="utf-8")
    snap = reg.status_report()
    after = (tmp_path / REGISTRY_PATH).read_text(encoding="utf-8")
    assert before == after  # reporting writes nothing
    assert snap["role_count"] == len(DEFAULT_ROLES)
    assert "recommendation_counts" in snap
    assert isinstance(snap["roles"], list)


def test_summary_line_is_compact_and_ascii(tmp_path: Path):
    reg = SubagentRegistry.load(tmp_path)
    line = reg.summary_line()
    assert line.startswith(f"{len(DEFAULT_ROLES)} roles")
    assert line.isascii()  # no mojibake in captured daemon logs


def test_record_lane_outcome_updates_but_is_not_auto_invoked(tmp_path: Path):
    # The method exists for future TD-023/TD-024 linking; it is only invoked
    # explicitly here (never automatically by producer/tick in this PR).
    reg = SubagentRegistry.load(tmp_path)
    assert reg.record_lane_outcome("reporter", "committed_local") is True
    assert reg.roles["reporter"].committed_local == 1
    assert reg.record_lane_outcome("reporter", "bogus") is False


def test_cost_units_default_zero_unknown_source(tmp_path: Path):
    # Per clarification 3: never invent per-role cost.
    reg = SubagentRegistry.load(tmp_path)
    reg.record_report(_full_proposed())
    for rec in reg.roles.values():
        assert rec.cost_units == 0.0
        assert rec.cost_source == "unknown"
