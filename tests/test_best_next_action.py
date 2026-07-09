from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from core.approval_inbox import ApprovalInboxItem
from core.approval_triage import triage_inbox
from core.best_next_action import (
    BestNextAction,
    format_best_next_action,
    select_best_next_action,
)
from core.self_build_memory import recent_unresolved_self_improvement_failures
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
