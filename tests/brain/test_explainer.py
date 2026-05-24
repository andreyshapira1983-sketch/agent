"""
tests/brain/test_explainer.py
"""
import pytest
from unittest.mock import MagicMock
from brain.explainer import Explainer, Explanation, RiskLevel
from brain.core import ThinkResult


# ------------------------------------------------------------------
# Fixtures & helpers
# ------------------------------------------------------------------

@pytest.fixture
def explainer():
    return Explainer()


def make_result(
    action: str = "respond",
    content: str = "Hello",
    confidence: float = 0.85,
    reasoning: str = "Clear intent",
    needs_human_approval: bool = False,
) -> ThinkResult:
    return ThinkResult(
        action=action,
        content=content,
        confidence=confidence,
        reasoning=reasoning,
        needs_human_approval=needs_human_approval,
    )


def rich_context() -> dict:
    return {
        "input": "What is the capital of France?",
        "history": [{"role": "user", "content": f"msg{i}"} for i in range(5)],
        "facts":   [{"text": "France is in Europe"}],
        "goals":   [{"text": "Answer user", "priority": 1}],
    }


def empty_context() -> dict:
    return {"input": "?", "history": [], "facts": [], "goals": []}


def make_uncertainty(calibrated: float = 0.8, threshold: float = 0.6, should_act: bool = True):
    u = MagicMock()
    u.calibrated_confidence = calibrated
    u.threshold_used = threshold
    u.should_act = should_act
    u.reasoning = f"Proceeding — calibrated score {calibrated:.0%} meets threshold {threshold:.0%}."
    u.signals = {
        "llm_confidence":   0.85,
        "context_quality":  0.7,
        "input_clarity":    1.0,
        "calibration_bias": 0.7,
        "calibrated":       calibrated,
        "threshold":        threshold,
    }
    return u


# ------------------------------------------------------------------
# Return type
# ------------------------------------------------------------------

def test_explain_returns_explanation(explainer):
    result = explainer.explain(make_result(), rich_context())
    assert isinstance(result, Explanation)


def test_explanation_has_all_fields(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert hasattr(exp, "action")
    assert hasattr(exp, "summary")
    assert hasattr(exp, "reasoning_chain")
    assert hasattr(exp, "risk_level")
    assert hasattr(exp, "factors")
    assert hasattr(exp, "recommended")
    assert hasattr(exp, "confidence")
    assert hasattr(exp, "needs_approval")


# ------------------------------------------------------------------
# Risk levels
# ------------------------------------------------------------------

def test_respond_is_low_risk(explainer):
    exp = explainer.explain(make_result(action="respond", confidence=0.9), rich_context())
    assert exp.risk_level == RiskLevel.LOW


def test_wait_is_low_risk(explainer):
    exp = explainer.explain(make_result(action="wait", confidence=0.5), rich_context())
    assert exp.risk_level == RiskLevel.LOW


def test_clarify_is_low_risk(explainer):
    exp = explainer.explain(make_result(action="clarify", confidence=0.6), rich_context())
    assert exp.risk_level == RiskLevel.LOW


def test_tool_call_with_high_confidence_is_medium(explainer):
    exp = explainer.explain(
        make_result(action="tool_call", confidence=0.8),
        rich_context(),
    )
    assert exp.risk_level == RiskLevel.MEDIUM


def test_tool_call_with_low_confidence_is_high(explainer):
    exp = explainer.explain(
        make_result(action="tool_call", confidence=0.3),
        empty_context(),
    )
    assert exp.risk_level == RiskLevel.HIGH


def test_tool_call_without_goals_is_high(explainer):
    exp = explainer.explain(
        make_result(action="tool_call", confidence=0.8),
        empty_context(),   # no goals
    )
    assert exp.risk_level == RiskLevel.HIGH


def test_stop_is_critical(explainer):
    exp = explainer.explain(
        make_result(action="stop", confidence=1.0),
        rich_context(),
    )
    assert exp.risk_level == RiskLevel.CRITICAL


def test_unknown_action_is_high_risk(explainer):
    exp = explainer.explain(
        make_result(action="do_weird_thing", confidence=0.9),
        rich_context(),
    )
    assert exp.risk_level == RiskLevel.HIGH


def test_respond_with_very_low_confidence_is_medium(explainer):
    exp = explainer.explain(
        make_result(action="respond", confidence=0.2),
        rich_context(),
    )
    assert exp.risk_level == RiskLevel.MEDIUM


# ------------------------------------------------------------------
# RiskLevel helpers
# ------------------------------------------------------------------

def test_risk_level_emoji():
    assert RiskLevel.LOW.emoji()      == "✅"
    assert RiskLevel.MEDIUM.emoji()   == "⚠️"
    assert RiskLevel.HIGH.emoji()     == "🔴"
    assert RiskLevel.CRITICAL.emoji() == "🚨"


# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------

def test_summary_is_nonempty_string(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert isinstance(exp.summary, str)
    assert len(exp.summary) > 0


def test_summary_mentions_action(explainer):
    action_keywords = {
        "respond":   "respond",
        "tool_call": "tool",
        "wait":      "paus",       # "pausing"
        "clarify":   "clarif",
        "stop":      "shutdown",   # "requesting shutdown"
    }
    for action, keyword in action_keywords.items():
        exp = explainer.explain(make_result(action=action, confidence=0.8), rich_context())
        assert keyword in exp.summary.lower(), f"Expected '{keyword}' in summary for action '{action}'"


def test_summary_mentions_confidence(explainer):
    exp = explainer.explain(make_result(confidence=0.75), rich_context())
    assert "75%" in exp.summary


# ------------------------------------------------------------------
# Reasoning chain
# ------------------------------------------------------------------

def test_reasoning_chain_is_list(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert isinstance(exp.reasoning_chain, list)
    assert len(exp.reasoning_chain) > 0


def test_reasoning_chain_contains_input(explainer):
    exp = explainer.explain(make_result(), rich_context())
    chain_text = " ".join(exp.reasoning_chain)
    assert "France" in chain_text


def test_reasoning_chain_contains_decision(explainer):
    exp = explainer.explain(make_result(action="respond"), rich_context())
    chain_text = " ".join(exp.reasoning_chain)
    assert "RESPOND" in chain_text or "respond" in chain_text.lower()


def test_reasoning_chain_includes_uncertainty_when_provided(explainer):
    u = make_uncertainty(calibrated=0.72, threshold=0.6)
    exp = explainer.explain(make_result(), rich_context(), uncertainty=u)
    chain_text = " ".join(exp.reasoning_chain)
    assert "72%" in chain_text or "0.72" in chain_text


# ------------------------------------------------------------------
# Factors
# ------------------------------------------------------------------

def test_factors_is_dict(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert isinstance(exp.factors, dict)


def test_factors_include_confidence(explainer):
    exp = explainer.explain(make_result(confidence=0.9), rich_context())
    assert "confidence" in exp.factors


def test_factors_include_goal_alignment(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert "goal_alignment" in exp.factors


def test_factors_high_confidence_label(explainer):
    exp = explainer.explain(make_result(confidence=0.9), rich_context())
    assert "High" in exp.factors["confidence"]


def test_factors_low_confidence_label(explainer):
    exp = explainer.explain(make_result(confidence=0.3), empty_context())
    assert "Low" in exp.factors["confidence"]


def test_approval_flag_in_factors(explainer):
    exp = explainer.explain(
        make_result(needs_human_approval=True),
        rich_context(),
    )
    assert "approval_required" in exp.factors


# ------------------------------------------------------------------
# for_human_approval()
# ------------------------------------------------------------------

def test_for_human_approval_is_string(explainer):
    exp = explainer.explain(
        make_result(action="tool_call", needs_human_approval=True),
        rich_context(),
    )
    text = exp.for_human_approval()
    assert isinstance(text, str)
    assert len(text) > 0


def test_for_human_approval_contains_action(explainer):
    exp = explainer.explain(
        make_result(action="tool_call", needs_human_approval=True),
        rich_context(),
    )
    assert "TOOL_CALL" in exp.for_human_approval()


def test_for_human_approval_contains_recommendation(explainer):
    exp = explainer.explain(
        make_result(action="stop", needs_human_approval=True, confidence=1.0),
        rich_context(),
    )
    text = exp.for_human_approval()
    assert "CRITICAL" in text or "critical" in text.lower()


def test_for_human_approval_contains_reasoning_steps(explainer):
    exp = explainer.explain(make_result(), rich_context())
    text = exp.for_human_approval()
    assert "1." in text   # numbered steps


# ------------------------------------------------------------------
# to_dict()
# ------------------------------------------------------------------

def test_to_dict_returns_dict(explainer):
    exp = explainer.explain(make_result(), rich_context())
    d = exp.to_dict()
    assert isinstance(d, dict)


def test_to_dict_contains_expected_keys(explainer):
    exp = explainer.explain(make_result(), rich_context())
    d = exp.to_dict()
    for key in ("action", "summary", "reasoning_chain", "risk_level",
                "factors", "recommended", "confidence", "needs_approval"):
        assert key in d


def test_to_dict_risk_level_is_string(explainer):
    exp = explainer.explain(make_result(), rich_context())
    assert isinstance(exp.to_dict()["risk_level"], str)


# ------------------------------------------------------------------
# Recommendation
# ------------------------------------------------------------------

def test_no_approval_needed_recommendation(explainer):
    exp = explainer.explain(make_result(needs_human_approval=False), rich_context())
    assert "automatic" in exp.recommended.lower() or "no approval" in exp.recommended.lower()


def test_critical_recommendation_warns(explainer):
    exp = explainer.explain(
        make_result(action="stop", needs_human_approval=True, confidence=1.0),
        rich_context(),
    )
    assert "critical" in exp.recommended.lower() or "cannot be undone" in exp.recommended.lower()


# ------------------------------------------------------------------
# Works without uncertainty
# ------------------------------------------------------------------

def test_works_without_uncertainty(explainer):
    exp = explainer.explain(make_result(), rich_context(), uncertainty=None)
    assert isinstance(exp, Explanation)
    assert exp.uncertainty_signals == {}


# ------------------------------------------------------------------
# Works with uncertainty
# ------------------------------------------------------------------

def test_uncertainty_signals_stored(explainer):
    u = make_uncertainty()
    exp = explainer.explain(make_result(), rich_context(), uncertainty=u)
    assert exp.uncertainty_signals == u.signals
