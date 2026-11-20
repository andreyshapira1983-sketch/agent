"""Clarification Gate: deterministic, LLM-free switch between PROCEED and CLARIFY (ask, do not build) mode.

With no signals it proceeds; any one ambiguity/stuck signal switches to CLARIFY with exactly one allowed action.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal


ClarificationMode = Literal["proceed", "clarify"]
AllowedAction = Literal["ask_question", "diagnostic_report", "minimal_next_step"]

# The only actions permitted in clarify mode; exactly one is chosen per outcome.
ALLOWED_IN_CLARIFY: tuple[AllowedAction, ...] = (
    "ask_question",
    "diagnostic_report",
    "minimal_next_step",
)

# Expensive or state-mutating actions that must not run while the goal or scope is unclear.
FORBIDDEN_IN_CLARIFY: tuple[str, ...] = (
    "spawn_subagent",
    "create_module",
    "write_memory",
    "create_proposal",
    "expensive_llm_call",
)



@dataclass(frozen=True)
class ClarificationSignals:
    """The ambiguity / stuck signals that route the agent into clarify mode; all default to False."""

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



@dataclass(frozen=True)
class ClarificationOutcome:
    mode: ClarificationMode
    triggered_by: tuple[str, ...]
    allowed_action: AllowedAction | None
    forbidden_actions: tuple[str, ...]
    questions: tuple[str, ...]
    reason: str

    @property
    def should_clarify(self) -> bool:
        return self.mode == "clarify"

    def is_forbidden(self, action: str) -> bool:
        """True iff ``action`` must NOT run given this outcome (clarify mode only)."""
        return self.mode == "clarify" and action in self.forbidden_actions

    def prompt(self) -> str:
        """Render the operator-facing message for clarify mode; empty string in proceed mode."""
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



# Canonical clarifying questions; each signal maps to the minimal subset, unioned in a stable order.
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



def evaluate(signals: ClarificationSignals) -> ClarificationOutcome:
    """Decide PROCEED vs CLARIFY for the given signals."""
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
    # with no concrete question to ask, fall back to a diagnostic report
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
    """Convenience: the clarify outcome for a suspected loop."""
    return evaluate(ClarificationSignals(
        loop_suspected=True,
        goal_too_broad=goal_too_broad,
    ))


def clarification_for_replan_exhausted() -> ClarificationOutcome:
    """``replan_exhausted`` is treated as ``loop_suspected``; shared by REPL, autonomous runtime and daemon tick."""
    return for_loop_suspected()


#: Stuck causes outside the task frame; asking "what to build" cannot resolve any of them.
_NOT_FRAME_CAUSES = frozenset({"unresolved_citation", "claim_refuted", "injection_blocked"})


def frame_questions_help(
    failure_codes: tuple[str, ...] | list[str], *, gathered: bool = False, frame_clear: bool = False,
) -> bool:
    """Whether a frame question (what to build, where, success criterion) can help.

    False when the stuck causes are all outside the frame, or when the turn gathered evidence and the frame is clear.
    Without known causes it asks as before.
    """
    if gathered and frame_clear:
        return False
    codes = set(failure_codes or ())
    return not codes or not codes <= _NOT_FRAME_CAUSES


# Fixed prefix: `format_human_response` buckets on it so the ask-back survives the section walk.
ASK_BACK_PREFIX = "Уточнение:"


def build_self_analysis_ask_back() -> str:
    """The deterministic ask-back for a self-analysis turn with zero verified support."""
    return (
        f"{ASK_BACK_PREFIX} я ответил рассуждением — без единой проверенной "
        "опоры, потому что понял вопрос как абстрактный, о моём устройстве "
        "или принципах. Если ты имел в виду что-то конкретное — файл, "
        "подсистему, задачу или недавний случай — назови это, и я отвечу "
        "с проверкой по коду и журналам. Что именно ты хочешь, чтобы я "
        "разобрал?"
    )
