"""
tests/brain/test_uncertainty.py
"""
import pytest
from brain.uncertainty import UncertaintyEstimator, UncertaintyResult


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def estimator():
    return UncertaintyEstimator()


def rich_context():
    return {
        "input": "What is the capital of France?",
        "history": [{"role": "user", "content": f"msg{i}"} for i in range(5)],
        "facts": [{"text": "France is in Europe"}, {"text": "Paris is a city"}],
        "goals": [{"text": "Answer user", "priority": 1}],
    }


def empty_context():
    return {"input": "?", "history": [], "facts": [], "goals": []}


# ------------------------------------------------------------------
# Return type
# ------------------------------------------------------------------

def test_estimate_returns_uncertainty_result(estimator):
    result = estimator.estimate(llm_confidence=0.8, context=rich_context())
    assert isinstance(result, UncertaintyResult)


def test_result_has_all_fields(estimator):
    result = estimator.estimate(llm_confidence=0.8, context=rich_context())
    assert hasattr(result, "calibrated_confidence")
    assert hasattr(result, "uncertainty_score")
    assert hasattr(result, "should_act")
    assert hasattr(result, "threshold_used")
    assert hasattr(result, "signals")
    assert hasattr(result, "reasoning")


# ------------------------------------------------------------------
# Confidence is clamped 0-1
# ------------------------------------------------------------------

def test_calibrated_confidence_in_range(estimator):
    r = estimator.estimate(llm_confidence=0.9, context=rich_context())
    assert 0.0 <= r.calibrated_confidence <= 1.0


def test_uncertainty_score_in_range(estimator):
    r = estimator.estimate(llm_confidence=0.9, context=rich_context())
    assert 0.0 <= r.uncertainty_score <= 1.0


def test_uncertainty_is_complement_of_confidence(estimator):
    r = estimator.estimate(llm_confidence=0.8, context=rich_context())
    assert abs(r.calibrated_confidence + r.uncertainty_score - 1.0) < 1e-6


def test_extreme_llm_confidence_clamped(estimator):
    r_high = estimator.estimate(llm_confidence=99.0, context=rich_context())
    r_low  = estimator.estimate(llm_confidence=-5.0, context=empty_context())
    assert r_high.calibrated_confidence <= 1.0
    assert r_low.calibrated_confidence  >= 0.0


# ------------------------------------------------------------------
# should_act logic
# ------------------------------------------------------------------

def test_high_confidence_should_act(estimator):
    r = estimator.estimate(llm_confidence=0.95, context=rich_context())
    assert r.should_act is True


def test_low_confidence_should_not_act(estimator):
    r = estimator.estimate(llm_confidence=0.1, context=empty_context())
    assert r.should_act is False


def test_should_act_equals_calibrated_above_threshold(estimator):
    r = estimator.estimate(llm_confidence=0.8, context=rich_context())
    expected = r.calibrated_confidence >= r.threshold_used
    assert r.should_act == expected


# ------------------------------------------------------------------
# Context quality affects score
# ------------------------------------------------------------------

def test_rich_context_raises_calibrated_confidence(estimator):
    r_rich  = estimator.estimate(llm_confidence=0.6, context=rich_context())
    r_empty = estimator.estimate(llm_confidence=0.6, context=empty_context())
    assert r_rich.calibrated_confidence > r_empty.calibrated_confidence


def test_goals_in_context_help_confidence(estimator):
    with_goals    = {"input": "do task", "history": [], "facts": [], "goals": [{"text": "Complete task"}]}
    without_goals = {"input": "do task", "history": [], "facts": [], "goals": []}
    r_with    = estimator.estimate(0.7, with_goals)
    r_without = estimator.estimate(0.7, without_goals)
    assert r_with.calibrated_confidence > r_without.calibrated_confidence


def test_empty_input_lowers_score(estimator):
    ctx_empty_input = {"input": "", "history": [], "facts": [], "goals": []}
    ctx_clear_input = {"input": "What is Python?", "history": [], "facts": [], "goals": []}
    r_empty = estimator.estimate(0.7, ctx_empty_input)
    r_clear = estimator.estimate(0.7, ctx_clear_input)
    assert r_clear.calibrated_confidence > r_empty.calibrated_confidence


# ------------------------------------------------------------------
# signals dict
# ------------------------------------------------------------------

def test_signals_contains_expected_keys(estimator):
    r = estimator.estimate(0.8, rich_context())
    for key in ("llm_confidence", "context_quality", "input_clarity",
                "calibration_bias", "calibrated", "threshold"):
        assert key in r.signals


def test_signals_values_in_range(estimator):
    r = estimator.estimate(0.8, rich_context())
    for key, val in r.signals.items():
        assert 0.0 <= val <= 1.0, f"Signal '{key}' out of range: {val}"


# ------------------------------------------------------------------
# calibrate() and adaptive threshold
# ------------------------------------------------------------------

def test_calibrate_does_not_crash(estimator):
    estimator.calibrate(predicted=0.8, was_correct=True)
    estimator.calibrate(predicted=0.5, was_correct=False)


def test_calibration_stats_empty(estimator):
    stats = estimator.calibration_stats()
    assert stats["samples"] == 0
    assert stats["accuracy"] is None


def test_calibration_stats_after_feedback(estimator):
    for _ in range(5):
        estimator.calibrate(0.8, was_correct=True)
    for _ in range(5):
        estimator.calibrate(0.8, was_correct=False)
    stats = estimator.calibration_stats()
    assert stats["samples"] == 10
    assert stats["accuracy"] == pytest.approx(0.5, abs=0.01)


def test_threshold_rises_after_overconfident_history(estimator):
    initial = estimator.threshold()
    # Overconfident: predicted high but always wrong
    for _ in range(20):
        estimator.calibrate(predicted=0.95, was_correct=False)
    assert estimator.threshold() > initial


def test_threshold_falls_after_underconfident_history(estimator):
    initial = estimator.threshold()
    # Underconfident: predicted low but always right
    for _ in range(20):
        estimator.calibrate(predicted=0.3, was_correct=True)
    assert estimator.threshold() < initial


def test_threshold_bounded_by_min_max(estimator):
    # Extreme underconfidence
    for _ in range(50):
        estimator.calibrate(predicted=0.0, was_correct=True)
    assert estimator.threshold() >= 0.40

    # Extreme overconfidence
    estimator2 = UncertaintyEstimator()
    for _ in range(50):
        estimator2.calibrate(predicted=1.0, was_correct=False)
    assert estimator2.threshold() <= 0.80


def test_threshold_does_not_change_before_min_samples(estimator):
    initial = estimator.threshold()
    # Only 5 samples — below MIN_SAMPLES_TO_ADAPT
    for _ in range(5):
        estimator.calibrate(predicted=0.9, was_correct=False)
    assert estimator.threshold() == initial


# ------------------------------------------------------------------
# reasoning is non-empty string
# ------------------------------------------------------------------

def test_reasoning_is_nonempty_string(estimator):
    r = estimator.estimate(0.8, rich_context())
    assert isinstance(r.reasoning, str)
    assert len(r.reasoning) > 0


def test_reasoning_mentions_decision(estimator):
    r_act    = estimator.estimate(0.95, rich_context())
    r_block  = estimator.estimate(0.05, empty_context())
    assert "proceeding" in r_act.reasoning.lower() or "proceed" in r_act.reasoning.lower()
    assert "block" in r_block.reasoning.lower()
