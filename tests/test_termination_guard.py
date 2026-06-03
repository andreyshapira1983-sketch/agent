"""Tests for core.termination_guard (MAST FM-1.5, FM-3.1)."""

from core.termination_guard import TerminationGuard


def test_no_event_on_first_attempt():
    g = TerminationGuard()
    ev = g.observe_attempt(
        attempt=1,
        failure_codes=["tool_error"],
        artifact_labels=[],
    )
    assert ev is None


def test_stagnation_fires_on_second_identical_signature():
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["tool_error"], artifact_labels=["a"])
    ev = g.observe_attempt(
        attempt=2, failure_codes=["tool_error"], artifact_labels=["a"]
    )
    assert ev is not None
    assert ev.attempt == 2
    assert ev.repeat_count == 2
    assert ev.failure_codes == ("tool_error",)


def test_stagnation_fires_only_once_per_streak():
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["x"], artifact_labels=[])
    first = g.observe_attempt(attempt=2, failure_codes=["x"], artifact_labels=[])
    second = g.observe_attempt(attempt=3, failure_codes=["x"], artifact_labels=[])
    assert first is not None
    assert second is None  # already reported


def test_changed_signature_resets_detector():
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["x"], artifact_labels=[])
    g.observe_attempt(attempt=2, failure_codes=["x"], artifact_labels=[])  # fires
    g.observe_attempt(attempt=3, failure_codes=["y"], artifact_labels=[])  # different
    rearmed = g.observe_attempt(
        attempt=4, failure_codes=["y"], artifact_labels=[]
    )
    assert rearmed is not None  # re-armed and fired again


def test_signature_is_order_independent():
    g = TerminationGuard()
    g.observe_attempt(
        attempt=1, failure_codes=["a", "b"], artifact_labels=["x", "y"]
    )
    ev = g.observe_attempt(
        attempt=2, failure_codes=["b", "a"], artifact_labels=["y", "x"]
    )
    assert ev is not None


def test_premature_completion_flagged_for_tool_demanding_question():
    g = TerminationGuard()
    ev = g.check_completion(
        question="прочитай core/loop.py и расскажи что там",
        chain_size=0,
        had_any_artifacts=False,
    )
    assert ev is not None
    assert any("прочит" in kw for kw in ev.matched_keywords)


def test_premature_completion_skipped_when_chain_nonempty():
    g = TerminationGuard()
    ev = g.check_completion(
        question="read the file",
        chain_size=3,
        had_any_artifacts=True,
    )
    assert ev is None


def test_premature_completion_skipped_for_general_question():
    g = TerminationGuard()
    ev = g.check_completion(
        question="What is the capital of France?",
        chain_size=0,
        had_any_artifacts=False,
    )
    assert ev is None


def test_log_payload_shapes():
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["x"], artifact_labels=[])
    ev = g.observe_attempt(attempt=2, failure_codes=["x"], artifact_labels=[])
    assert ev is not None
    payload = ev.to_log_payload()
    assert payload["attempt"] == 2
    assert payload["failure_codes"] == ["x"]

    pc = g.check_completion(
        question="run the tests please",
        chain_size=0,
        had_any_artifacts=False,
    )
    assert pc is not None
    p2 = pc.to_log_payload()
    assert p2["chain_size"] == 0
    assert "matched_keywords" in p2
