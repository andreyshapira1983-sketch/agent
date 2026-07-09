"""Clarification Gate — режим переспроса (ask, don't build).

When the agent is stuck (``loop_suspected``) or the goal is too broad /
ambiguous, the mature response is **not** to create chaos — spawning
subagents, writing new modules, writing memory, spraying proposals, or burning
more expensive LLM calls. It is to do what a competent human would do: stop,
narrow the frame, and **ask one clarifying question** (or take one minimal
diagnostic step).

The guiding formula (operator's words):

    Если агент не понимает, что строить, он должен не строить,
    а спросить, что значит "построить".

This module is a pure, deterministic POLICY. Given a set of signals it decides
whether to PROCEED or switch to CLARIFY mode, and in CLARIFY mode it enumerates:

* the single allowed action (ask a question / one diagnostic report / one
  minimal next step);
* the forbidden actions (the "chaos" set that must NOT run while unclear);
* the minimal clarifying questions to put to the operator.

Design principles
-----------------
* No LLM calls, no I/O, mutates nothing — deterministic and O(n).
* Conservative: with zero signals it returns PROCEED (never blocks a clear
  task). Any one ambiguity/stuck signal switches to CLARIFY.
* In CLARIFY mode it commits to exactly ONE allowed action so the agent cannot
  "do everything" — the whole point is to reduce chaos, not add to it.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------

ClarificationMode = Literal["proceed", "clarify"]
AllowedAction = Literal["ask_question", "diagnostic_report", "minimal_next_step"]

# The ONLY actions permitted while the agent is in clarify mode. Exactly one of
# these is chosen per outcome — never several at once.
ALLOWED_IN_CLARIFY: tuple[AllowedAction, ...] = (
    "ask_question",
    "diagnostic_report",
    "minimal_next_step",
)

# The "chaos" set: expensive or state-mutating actions that must NOT run while
# the goal/scope is unclear. These are precisely what a bad agent does when it
# doesn't understand the task; the gate forbids them until the human narrows
# the frame.
FORBIDDEN_IN_CLARIFY: tuple[str, ...] = (
    "spawn_subagent",
    "create_module",
    "write_memory",
    "create_proposal",
    "expensive_llm_call",
)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClarificationSignals:
    """The ambiguity / stuck signals that route the agent into clarify mode.

    Every field defaults to ``False`` (the clear, safe-to-proceed case), so a
    caller only sets the signals it actually observed.
    """

    goal_too_broad: bool = False
    no_success_criterion: bool = False
    no_target: bool = False                  # no target file / source
    unclear_where: bool = False              # unclear where to execute
    unclear_live_effect: bool = False        # unclear if a live effect is wanted
    loop_suspected: bool = False             # the usefulness brake fired
    tied_best_actions: bool = False          # several BNA of equal strength
    subagents_disagree_weak_evidence: bool = False  # spread + weak evidence

    def any_set(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def triggered(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name))


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClarificationOutcome:
    mode: ClarificationMode
    triggered_by: tuple[str, ...]
    allowed_action: Optional[AllowedAction]
    forbidden_actions: tuple[str, ...]
    questions: tuple[str, ...]
    reason: str

    @property
    def should_clarify(self) -> bool:
        return self.mode == "clarify"

    def is_forbidden(self, action: str) -> bool:
        """True iff ``action`` must NOT run given this outcome.

        Only meaningful in clarify mode; in proceed mode nothing is forbidden.
        """
        return self.mode == "clarify" and action in self.forbidden_actions

    def prompt(self) -> str:
        """Render the operator-facing message for clarify mode.

        Empty string in proceed mode (nothing to ask).
        """
        if self.mode != "clarify" or not self.questions:
            return ""
        lines = ["Я не могу безопасно продолжить. Уточни:"]
        lines.extend(f"  - {q}" for q in self.questions)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "triggered_by": list(self.triggered_by),
            "allowed_action": self.allowed_action,
            "forbidden_actions": list(self.forbidden_actions),
            "questions": list(self.questions),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Question registry
# ---------------------------------------------------------------------------

# Canonical clarifying questions (operator's language). Each signal maps to the
# minimal subset that resolves it; the union is taken in this stable order so
# repeated signals never duplicate or reorder a question.
_Q_WHAT = "Что именно нужно построить?"
_Q_PURPOSE = "Для какой цели?"
_Q_WHERE = "Где это должно работать (target file / source / окружение)?"
_Q_SUCCESS = "Что считать готовым результатом (критерий успеха)?"
_Q_FORBIDDEN = "Какие действия запрещены / что нельзя трогать?"
_Q_BUDGET = "Сколько можно потратить времени и денег?"
_Q_PLAN_OR_DO = "Нужно ли просто предложить план или реально выполнить (live-effect)?"
_Q_PRIORITY = "Несколько действий равны по приоритету — какое берём первым?"
_Q_SOURCE_OF_TRUTH = (
    "Подагенты расходятся при слабых данных — какой источник истины предпочесть?"
)

# Stable rendering order for the union of questions.
_QUESTION_ORDER: tuple[str, ...] = (
    _Q_WHAT,
    _Q_PURPOSE,
    _Q_WHERE,
    _Q_SUCCESS,
    _Q_FORBIDDEN,
    _Q_BUDGET,
    _Q_PLAN_OR_DO,
    _Q_PRIORITY,
    _Q_SOURCE_OF_TRUTH,
)

# signal name -> questions it contributes.
_SIGNAL_QUESTIONS: dict[str, tuple[str, ...]] = {
    "goal_too_broad": (_Q_WHAT, _Q_PURPOSE),
    "no_success_criterion": (_Q_SUCCESS,),
    "no_target": (_Q_WHAT, _Q_WHERE),
    "unclear_where": (_Q_WHERE,),
    "unclear_live_effect": (_Q_PLAN_OR_DO,),
    # When stuck we ask the three human core questions: what, where, success.
    "loop_suspected": (_Q_WHAT, _Q_WHERE, _Q_SUCCESS),
    "tied_best_actions": (_Q_PRIORITY,),
    "subagents_disagree_weak_evidence": (_Q_SOURCE_OF_TRUTH,),
}

# Human reason, strongest trigger first.
_REASON_PRIORITY: tuple[tuple[str, str], ...] = (
    ("loop_suspected", "застрял (loop_suspected) — переход в режим вопроса, не стройки"),
    ("subagents_disagree_weak_evidence",
     "подагенты расходятся при слабом evidence — нужен выбор источника истины"),
    ("tied_best_actions", "несколько действий равны по приоритету — нужен выбор"),
    ("goal_too_broad", "цель слишком широкая — нужно сузить рамку"),
    ("no_success_criterion", "нет критерия успеха"),
    ("no_target", "нет target file / source"),
    ("unclear_where", "непонятно, где выполнять"),
    ("unclear_live_effect", "непонятно, нужен ли live-effect"),
)


def _questions_for(signals: ClarificationSignals) -> tuple[str, ...]:
    wanted: set[str] = set()
    for name in signals.triggered():
        wanted.update(_SIGNAL_QUESTIONS.get(name, ()))
    return tuple(q for q in _QUESTION_ORDER if q in wanted)


def _reason_for(signals: ClarificationSignals) -> str:
    fired = set(signals.triggered())
    for name, reason in _REASON_PRIORITY:
        if name in fired:
            return reason
    return "ambiguous request"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def evaluate(signals: ClarificationSignals) -> ClarificationOutcome:
    """Decide PROCEED vs CLARIFY for the given signals.

    With no signals set the agent is clear to act (PROCEED). Any one ambiguity
    or stuck signal flips it to CLARIFY: it commits to exactly ONE allowed
    action (ask the operator), forbids the chaos set, and returns the minimal
    set of clarifying questions.
    """
    if not signals.any_set():
        return ClarificationOutcome(
            mode="proceed",
            triggered_by=(),
            allowed_action=None,
            forbidden_actions=(),
            questions=(),
            reason="all signals clear; safe to proceed",
        )

    questions = _questions_for(signals)
    # The single human action: ask. If somehow no concrete question maps (every
    # mapped signal absent), fall back to a diagnostic report rather than ask an
    # empty question.
    allowed: AllowedAction = "ask_question" if questions else "diagnostic_report"
    return ClarificationOutcome(
        mode="clarify",
        triggered_by=signals.triggered(),
        allowed_action=allowed,
        forbidden_actions=FORBIDDEN_IN_CLARIFY,
        questions=questions,
        reason=_reason_for(signals),
    )


def for_loop_suspected(*, goal_too_broad: bool = False) -> ClarificationOutcome:
    """Convenience: the clarify outcome for a suspected loop.

    ``loop_suspected`` alone is enough to switch into the question mode; callers
    that also know the goal is broad can set ``goal_too_broad`` to widen the
    asked questions.
    """
    return evaluate(ClarificationSignals(
        loop_suspected=True,
        goal_too_broad=goal_too_broad,
    ))


def clarification_for_replan_exhausted() -> ClarificationOutcome:
    """TD-032 slice 3 — ``replan_exhausted`` is stuck equivalence for ``loop_suspected``.

    Shared by REPL, autonomous runtime, and daemon tick so unattended paths reuse
    the same deterministic clarify semantics without new policy rules.
    """
    return for_loop_suspected()
