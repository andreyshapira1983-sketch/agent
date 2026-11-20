"""Strategy Router: deterministic, pure keyword routing of operator input before the LLM planner.

``general_question`` is the safe default; explicit documentation requests never map to a local strategy.
"""
from __future__ import annotations

from enum import Enum

from core.operator_intent import route_operator_intent


class OperatorStrategy(str, Enum):
    """Routing decision produced by the Strategy Router."""

    safe_self_check       = "safe_self_check"
    capability_request    = "capability_request"
    self_build_request    = "self_build_request"
    subagent_proposal     = "subagent_proposal"
    architecture_audit    = "architecture_audit"
    self_task_proposal    = "self_task_proposal"
    capability_check      = "capability_check"
    programming_readiness = "programming_readiness"
    current_gaps_check    = "current_gaps_check"
    weakness_finder       = "weakness_finder"
    next_safe_test        = "next_safe_test"
    project_health        = "project_health"
    model_status          = "model_status"
    budget_status         = "budget_status"
    approval_status       = "approval_status"
    urgent_status         = "urgent_status"
    best_next_action      = "best_next_action"
    next_actions          = "next_actions"
    autonomy_readiness    = "autonomy_readiness"
    source_review_plan    = "source_review_plan"
    implementation_plan   = "implementation_plan"
    patch_proposal        = "patch_proposal"
    shell_command_hint    = "shell_command_hint"
    # Catch-all — forward to LLM planner
    general_question      = "general_question"


#: Strategies that are handled locally — no LLM call is needed.
LOCAL_STRATEGIES: frozenset[OperatorStrategy] = frozenset(
    s for s in OperatorStrategy if s is not OperatorStrategy.general_question
)


def classify_operator_strategy(text: str) -> OperatorStrategy:
    """Map operator natural-language input to a routing strategy; never raises.

    Returns ``general_question`` when no local handler matches, the text asks for documentation, or the input is not a string.
    """
    if not isinstance(text, str):
        return OperatorStrategy.general_question
    intent = route_operator_intent(text)
    if intent is None:
        return OperatorStrategy.general_question
    try:
        return OperatorStrategy(intent.kind)
    except ValueError:
        # unknown intent kind (not yet in this enum): safe fallback
        return OperatorStrategy.general_question


def is_local_strategy(strategy: OperatorStrategy) -> bool:
    """Return True when *strategy* can be handled without the LLM planner."""
    return strategy in LOCAL_STRATEGIES
