"""Strategy Router: deliberation kernel layer BEFORE the LLM planner.

``classify_operator_strategy`` maps operator natural-language input to an
``OperatorStrategy`` enum value with zero I/O and zero LLM calls.  The
caller uses the strategy to decide whether to invoke a local handler or
forward the request to the LLM planner.

    strategy = classify_operator_strategy(user_text)
    if strategy is OperatorStrategy.general_question:
        # forward to planner / LLM
        ...
    else:
        # dispatch to the local handler registered for that strategy
        ...

Safety contract
---------------
* Pure function — no file I/O, no network, no LLM calls.
* Deterministic — same input always returns the same strategy.
* ``general_question`` is the safe default: when in doubt, let the LLM
  handle it rather than silently routing to a wrong local handler.
* Explicit documentation requests ("по README", "по документации", …) are
  NOT captured by any local strategy — they always map to
  ``general_question`` so the LLM can read the docs as requested.

Strategies
----------
All local strategies (anything except ``general_question``) are handled by
existing handlers in ``main.py`` via ``handle_conversational_operator_input``.
The handlers call live system modules (architecture audit, budget ledger,
task queue, …) without issuing LLM calls or reading README/docs unless
explicitly asked.
"""
from __future__ import annotations

from enum import Enum

from core.operator_intent import route_operator_intent


class OperatorStrategy(str, Enum):
    """Routing decision produced by the Strategy Router.

    Members map 1-to-1 to ``OperatorIntentKind`` literals (so existing
    dispatch tables in ``main.py`` continue to work without change).
    ``general_question`` is the catch-all: the LLM planner handles it.
    """

    safe_self_check       = "safe_self_check"
    capability_request    = "capability_request"
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
    """Map operator natural-language input to a routing strategy.

    Returns ``OperatorStrategy.general_question`` when:
    - no local handler matches the text, OR
    - the operator explicitly requests documentation ("по README", …)
      so the LLM can read and summarise the actual docs.

    Non-string inputs are treated as unrecognised (``general_question``).

    Parameters
    ----------
    text:
        Raw operator input (any language, any casing).

    Returns
    -------
    OperatorStrategy
        Always a valid enum member; never raises.
    """
    if not isinstance(text, str):
        return OperatorStrategy.general_question
    intent = route_operator_intent(text)
    if intent is None:
        return OperatorStrategy.general_question
    try:
        return OperatorStrategy(intent.kind)
    except ValueError:
        # Unknown intent kind added to operator_intent.py without a
        # matching enum member here — safe fallback.
        return OperatorStrategy.general_question


def is_local_strategy(strategy: OperatorStrategy) -> bool:
    """Return True when *strategy* can be handled without the LLM planner."""
    return strategy in LOCAL_STRATEGIES
