from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cli.commands_approval import _handle_self_issue_verify
from core.approval_inbox import ApprovalInboxItem
from core.approval_triage import triage_inbox
from core.best_next_action import (
    BestNextAction,
    format_best_next_action,
    select_best_next_action,
)
from core.self_build_memory import (
    recent_unresolved_self_improvement_failures,
    sync_self_improvement_issue_registry,
)
from core.self_improvement_issues import (
    DEFAULT_ISSUE_PATH,
    SelfImprovementIssueRegistry,
)
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _triage_with_duplicates(n_dupes: int):
    items = [
        ApprovalInboxItem(
            operation="proposed_task",
            summary=f"dup #{i}",
            payload={"canonical_signature": "tests:dup", "rationale": "x"},
            reasons=("r",),
            id=f"ain_{i}",
            created_at=f"2026-06-04T0{i}:00:00+00:00",
            updated_at=f"2026-06-04T0{i}:00:00+00:00",
        )
        for i in range(n_dupes + 1)  # +1 original kept
    ]
    return triage_inbox(items)


def test_returns_exactly_one_action_even_when_nothing_pressing():
    action = select_best_next_action()
    assert isinstance(action, BestNextAction)
    assert action.action == "observe"
    assert action.severity == "none"
    assert action.priority == 0


def test_failing_tests_beat_inbox_debt_and_dry_run_stall():
    triage = _triage_with_duplicates(5)
    action = select_best_next_action(
        tests_health="fail",
        result_status="failed",
        failed_tests=("test_a", "test_b"),
        dry_run_streak=10,
        triage=triage,
    )
    assert action.action == "propose_minimal_test_repair"
    assert action.severity == "high"
    # Evidence names the concrete failing tests.
    assert any("test_a" in e for e in action.evidence)
    assert action.recommended_command == ":propose-repair"


def test_daemon_down_outranks_everything():
    action = select_best_next_action(
        heartbeat_missing=True,
        tests_health="fail",
        failed_tests=("test_x",),
        dry_run_streak=99,
    )
    assert action.action == "restore_daemon_liveness"
    assert action.severity == "critical"
    assert action.priority == 100


def test_tick_error_outranks_test_failure():
    action = select_best_next_action(
        tick_error="RuntimeError: boom",
        tests_health="fail",
        failed_tests=("test_x",),
    )
    assert action.action == "investigate_tick_error"
    assert any("boom" in e for e in action.evidence)


def test_inconclusive_tests_are_not_reported_as_healthy():
    action = select_best_next_action(tests_health="inconclusive")
    assert action.action == "resolve_inconclusive_tests"
    # The honest unknown must explicitly state health is NOT established.
    assert any("NO verdict" in u or "healthy" in u for u in action.unknowns)
    assert action.severity == "high"


def test_inbox_duplicate_debt_recommends_triage_when_no_test_problem():
    triage = _triage_with_duplicates(4)
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        triage=triage,
    )
    assert action.action == "reduce_inbox_duplicate_debt"
    assert action.recommended_command == ":approval-triage"
    assert any("duplicate" in e for e in action.evidence)


def test_dry_run_stall_surfaces_after_threshold():
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        dry_run_streak=7,
    )
    assert action.action == "review_dry_run_stall"
    assert any("dry_run_streak=7" in e for e in action.evidence)
    # Enabling effects is risky — must be flagged, not auto-done.
    assert action.risk == "external"


def test_short_dry_run_streak_does_not_trigger_stall():
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        dry_run_streak=2,
    )
    assert action.action == "observe"


def test_recent_self_improvement_failure_beats_clean_observe_state():
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        inbox_pending=0,
        recent_self_improvement_failures=(
            "self-apply rolled_back: TypeError: duplicate base class AgentLoopExtractedMethods",
            "repair proposal rejected: proposal changes too many lines (480 > 200)",
        ),
    )
    assert action.action == "repair_incremental_splitter_duplicate_mixin"
    assert action.risk == "read_only"
    assert "incremental_splitter.py" in (action.recommended_command or "")
    assert any("too many lines" in item for item in action.evidence)


def test_recent_failure_scanner_reads_rollback_memory_and_rejected_repair(workspace):
    now = datetime.now(timezone.utc).isoformat()
    store = EpisodicMemoryStore(workspace / "data" / "episodic_memory.jsonl")
    store.save(EpisodeRecord(
        goal="apply self-build proposal",
        question="self-apply-run",
        outcome="failed",
        summary="self-apply rolled_back: TypeError: duplicate base class AgentLoopExtractedMethods",
        tags=("self-build", "lesson", "rolled_back", "failed"),
        created_at=now,
    ))
    log_dir = workspace / "logs"
    log_dir.mkdir()
    (log_dir / "recent.jsonl").write_text(json.dumps({
        "ts": now,
        "event": "repair_proposal_result",
        "payload": {
            "status": "rejected",
            "warnings": ["proposal changes too many lines (480 > 200)"],
        },
    }) + "\n", encoding="utf-8")

    signals = recent_unresolved_self_improvement_failures(
        SimpleNamespace(episodic_store=store), workspace
    )
    assert any("duplicate base class" in item for item in signals)
    assert any("too many lines" in item for item in signals)


def _agent_with_duplicate_mixin_failure(workspace, created_at: str):
    store = EpisodicMemoryStore(workspace / "data" / "episodic_memory.jsonl")
    store.save(EpisodeRecord(
        goal="apply self-build proposal",
        question="self-apply-run",
        outcome="failed",
        summary="self-apply rolled_back: TypeError: duplicate base class AgentLoopExtractedMethods",
        tags=("self-build", "lesson", "rolled_back", "failed"),
        created_at=created_at,
    ))
    return SimpleNamespace(episodic_store=store)


def test_duplicate_mixin_failure_creates_one_durable_open_issue(workspace):
    now = datetime.now(timezone.utc).isoformat()
    agent = _agent_with_duplicate_mixin_failure(workspace, now)

    registry = sync_self_improvement_issue_registry(agent, workspace)
    first = registry.list()
    sync_self_improvement_issue_registry(agent, workspace)
    second = registry.list()

    assert len(first) == len(second) == 1
    assert first[0].fingerprint == second[0].fingerprint
    assert first[0].status == "open"
    assert first[0].title
    assert first[0].action == "repair_incremental_splitter_duplicate_mixin"
    assert first[0].first_seen == first[0].last_seen == now
    assert first[0].evidence
    assert "duplicate base class" in first[0].related_error_text
    assert first[0].suggested_next_action
    assert first[0].related_files == (
        "core/incremental_splitter.py",
        "tests/test_incremental_splitter.py",
    )
    assert registry.path.is_file()


def test_unrelated_successful_self_apply_does_not_resolve_issue(workspace):
    now = datetime.now(timezone.utc)
    agent = _agent_with_duplicate_mixin_failure(workspace, now.isoformat())
    agent.episodic_store.save(EpisodeRecord(
        goal="apply unrelated self-build proposal",
        question="self-apply-run",
        outcome="success",
        summary="self-apply committed_local: unrelated tests passed",
        tags=("self-build", "self-apply-run", "success"),
        created_at=(now + timedelta(seconds=1)).isoformat(),
    ))

    registry = sync_self_improvement_issue_registry(agent, workspace)
    assert [issue.status for issue in registry.list()] == ["open"]
    assert len(registry.unresolved()) == 1


def test_matching_resolution_marker_resolves_issue(workspace):
    now = datetime.now(timezone.utc)
    agent = _agent_with_duplicate_mixin_failure(workspace, now.isoformat())
    registry = sync_self_improvement_issue_registry(agent, workspace)
    fingerprint = registry.unresolved()[0].fingerprint
    log_dir = workspace / "logs"
    log_dir.mkdir()
    (log_dir / "resolution.jsonl").write_text(json.dumps({
        "ts": (now + timedelta(seconds=1)).isoformat(),
        "event": "self_improvement_issue_resolved",
        "payload": {
            "fingerprint": fingerprint,
            "evidence": "targeted duplicate-mixin regression test passed",
        },
    }) + "\n", encoding="utf-8")

    registry = sync_self_improvement_issue_registry(agent, workspace)
    assert registry.list()[0].status == "resolved"
    assert registry.unresolved() == []


def test_best_next_action_prefers_open_registry_issue_over_raw_history():
    action = select_best_next_action(
        open_self_improvement_issues=({
            "fingerprint": "sii_registry",
            "title": "Registry-owned issue",
            "action": "registry_specific_action",
            "status": "open",
            "evidence": ["durable evidence"],
            "related_files": ["core/specific.py"],
            "suggested_next_action": "inspect core/specific.py",
        },),
        recent_self_improvement_failures=(
            "TypeError: duplicate base class AgentLoopExtractedMethods",
        ),
    )
    assert action.action == "registry_specific_action"
    assert any("sii_registry" in item for item in action.evidence)
    assert all("AgentLoopExtractedMethods" not in item for item in action.evidence)


class _IssueLog:
    def __init__(self):
        self.events = []

    def log(self, event, payload):
        self.events.append((event, payload))


class _PassingDuplicateMixinVerifier:
    def __init__(self, workspace_root):
        self.workspace_root = workspace_root

    def run(self, *, paths, pattern):
        assert paths == ["tests/test_incremental_splitter.py"]
        assert pattern == "test_repeated_mixin_split_uses_unique_base_class"
        return {
            "exit_code": 0,
            "timed_out": False,
            "passed": 1,
            "failed": 0,
            "errors": 0,
        }

    def validate_output(self, output):
        return True, []


def _open_duplicate_issue(workspace):
    registry = SelfImprovementIssueRegistry(workspace / DEFAULT_ISSUE_PATH)
    issue = registry.upsert_failure(
        "TypeError: duplicate base class AgentLoopExtractedMethods",
        datetime.now(timezone.utc).isoformat(),
    )
    return registry, issue


def test_self_issue_verify_matching_regression_resolves_issue(
    workspace, monkeypatch, capsys
):
    registry, issue = _open_duplicate_issue(workspace)
    monkeypatch.setattr(
        "tools.run_tests.RunTestsTool", _PassingDuplicateMixinVerifier
    )
    log = _IssueLog()

    assert _handle_self_issue_verify(
        issue.fingerprint, SimpleNamespace(log=log), workspace
    ) is True

    assert registry.list()[0].status == "resolved"
    assert [event for event, _payload in log.events] == [
        "self_improvement_issue_verified",
        "self_improvement_issue_resolved",
    ]
    assert "issue resolved" in capsys.readouterr().err


def test_self_issue_verify_resolved_transition_conflict_stays_unresolved(
    workspace, monkeypatch, capsys
):
    registry, issue = _open_duplicate_issue(workspace)
    monkeypatch.setattr(
        "tools.run_tests.RunTestsTool", _PassingDuplicateMixinVerifier
    )
    original_transition = SelfImprovementIssueRegistry.transition

    def _refuse_resolution(self, *, status, **kwargs):
        if status == "resolved":
            return None
        return original_transition(self, status=status, **kwargs)

    monkeypatch.setattr(SelfImprovementIssueRegistry, "transition", _refuse_resolution)
    log = _IssueLog()

    assert _handle_self_issue_verify(
        issue.fingerprint, SimpleNamespace(log=log), workspace
    ) is True

    assert registry.list()[0].status == "verified"
    assert [event for event, _payload in log.events] == [
        "self_improvement_issue_verified",
    ]
    error = capsys.readouterr().err
    assert "remains unresolved" in error
    assert "issue resolved" not in error


def test_self_issue_verify_infrastructure_error_stays_open(
    workspace, monkeypatch, capsys
):
    registry, issue = _open_duplicate_issue(workspace)

    class _BrokenVerifier:
        def __init__(self, workspace_root):
            self.workspace_root = workspace_root

        def run(self, *, paths, pattern):
            raise OSError("python launch failed")

    monkeypatch.setattr("tools.run_tests.RunTestsTool", _BrokenVerifier)
    log = _IssueLog()

    assert _handle_self_issue_verify(
        issue.fingerprint, SimpleNamespace(log=log), workspace
    ) is True

    assert registry.list()[0].status == "open"
    assert log.events == []
    error = capsys.readouterr().err
    assert "remains unresolved" in error
    assert "verifier infrastructure error: OSError" in error
    assert "python launch failed" not in error


def test_self_issue_verify_wrong_fingerprint_or_action_cannot_resolve(
    workspace, monkeypatch, capsys
):
    registry, issue = _open_duplicate_issue(workspace)

    class _MustNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unmatched issue must not run tests")

    monkeypatch.setattr("tools.run_tests.RunTestsTool", _MustNotRun)
    log = _IssueLog()
    agent = SimpleNamespace(log=log)
    _handle_self_issue_verify("sii_wrong", agent, workspace)
    assert registry.list()[0].status == "open"

    generic = registry.upsert_failure(
        "self-build failed with an unsupported generic error",
        (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    )
    _handle_self_issue_verify(generic.fingerprint, agent, workspace)
    states = {item.fingerprint: item.status for item in registry.list()}
    assert states[issue.fingerprint] == states[generic.fingerprint] == "open"
    assert log.events == []
    error = capsys.readouterr().err
    assert "unknown fingerprint" in error
    assert "no targeted verifier" in error


def test_resolved_registry_suppresses_stale_raw_duplicate_mixin_history():
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        self_improvement_registry_available=True,
        open_self_improvement_issues=(),
        recent_self_improvement_failures=(
            "TypeError: duplicate base class AgentLoopExtractedMethods",
        ),
    )
    assert action.action == "observe"


def test_large_backlog_without_duplicates_recommends_review():
    action = select_best_next_action(
        tests_health="pass",
        result_status="done",
        inbox_pending=20,
    )
    assert action.action == "review_inbox_backlog"
    assert any("20 pending" in e for e in action.evidence)


def test_every_action_carries_evidence_and_unknowns():
    for action in (
        select_best_next_action(heartbeat_missing=True),
        select_best_next_action(tick_error="x"),
        select_best_next_action(tests_health="fail", failed_tests=("t",)),
        select_best_next_action(tests_health="inconclusive"),
        select_best_next_action(triage=_triage_with_duplicates(4)),
        select_best_next_action(dry_run_streak=9),
        select_best_next_action(inbox_pending=20),
        select_best_next_action(),
    ):
        assert action.evidence, f"{action.action} has no evidence"
        assert action.unknowns, f"{action.action} has no unknowns"
        assert 0.0 <= action.confidence <= 1.0


def test_selection_is_deterministic_for_same_signals():
    kwargs = dict(tests_health="fail", failed_tests=("a",), dry_run_streak=8)
    first = select_best_next_action(**kwargs)
    second = select_best_next_action(**kwargs)
    assert first.to_dict() == second.to_dict()


def test_format_block_is_advisory_and_compact():
    action = select_best_next_action(tests_health="fail", failed_tests=("test_a",))
    text = format_best_next_action(action)
    assert "best next action:" in text
    assert "I do NOT know:" in text
    assert "advisory only" in text
    # Compact: a handful of lines, not a wall of text.
    assert text.count("\n") < 20


def test_to_dict_round_trips_shape():
    action = select_best_next_action(tests_health="fail", failed_tests=("t",))
    data = action.to_dict()
    assert data["action"] == "propose_minimal_test_repair"
    assert isinstance(data["evidence"], list)
    assert isinstance(data["unknowns"], list)
    assert "recommended_command" in data
