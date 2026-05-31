"""Conversational routing for operator-control requests.

The CLI still exposes explicit `:commands`, but the long-term operator UX
should let the owner ask normal status questions. This module keeps that
translation deterministic and local, so common control-plane requests do not
need an LLM call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OperatorIntentKind = Literal[
    "shell_command_hint",
    "project_health",
    "model_status",
    "budget_status",
    "approval_status",
    "urgent_status",
    "next_actions",
    "autonomy_readiness",
    "source_review_plan",
]


@dataclass(frozen=True)
class OperatorIntent:
    kind: OperatorIntentKind
    command: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "command": self.command,
            "reason": self.reason,
        }


def route_operator_intent(text: str) -> OperatorIntent | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if _looks_like_shell_command(normalized):
        return OperatorIntent(
            kind="shell_command_hint",
            command="shell-command-hint",
            reason="shell/powershell command wording",
        )
    if _matches_implementation_or_source_review(normalized):
        return OperatorIntent(
            kind="source_review_plan",
            command=":source-review-plan",
            reason="implementation/source-review planning wording",
        )
    if _matches_project_health(normalized):
        return OperatorIntent(
            kind="project_health",
            command=":operator-check",
            reason="project health/status wording",
        )
    if _matches_urgent_status(normalized):
        return OperatorIntent(
            kind="urgent_status",
            command=":urgent-status",
            reason="urgent attention wording",
        )
    if _matches_next_actions(normalized):
        return OperatorIntent(
            kind="next_actions",
            command=":next-actions",
            reason="next-step wording",
        )
    if _matches_autonomy_readiness(normalized):
        return OperatorIntent(
            kind="autonomy_readiness",
            command=":autonomy-readiness",
            reason="autonomy readiness wording",
        )
    if _matches_model_status(normalized):
        return OperatorIntent(
            kind="model_status",
            command=":models",
            reason="model routing/status wording",
        )
    if _matches_budget_status(normalized):
        return OperatorIntent(
            kind="budget_status",
            command=":operator-budget",
            reason="budget/token/spend wording",
        )
    if _matches_approval_status(normalized):
        return OperatorIntent(
            kind="approval_status",
            command=":approval-list all",
            reason="approval inbox wording",
        )
    return None


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _looks_like_shell_command(text: str) -> bool:
    command_prefixes = (
        "py ",
        "python ",
        "pwsh",
        "powershell",
        "git ",
        "pytest",
        "pip ",
        "test-path",
        "get-childitem",
        "new-item",
        "move-item",
        "set-content",
        ".\\main.py",
        "./main.py",
    )
    command_markers = (
        " --auto-approve",
        " --file",
    )
    stripped = text.strip()
    if any(stripped.startswith(prefix) for prefix in command_prefixes):
        return True
    return _has_any(f" {stripped}", command_markers)


def _matches_implementation_or_source_review(text: str) -> bool:
    planning_terms = (
        "implementation plan",
        "план реализации",
        "составь точный план",
        "точный план реализации",
        "какие файлы менять",
        "какие тесты добавить",
        "operator task layer",
    )
    source_review_terms = (
        "сравни загруженные источники",
        "сравнить загруженные источники",
        "source review",
        "review loaded sources",
        "file comparison",
        "сравни файлы",
        "сравнить файлы",
    )
    filename_markers = (".py", ".md", ".txt", "\\", "/")
    if _has_any(text, planning_terms) or _has_any(text, source_review_terms):
        return True
    return _has_any(text, filename_markers) and _has_any(
        text,
        (
            "сравни",
            "сравнить",
            "план",
            "реализац",
            "implementation",
            "review",
            "compare",
        ),
    )


def _matches_project_health(text: str) -> bool:
    direct_phrases = (
        "проверь проект",
        "проверить проект",
        "проверка проекта",
        "статус проекта",
        "состояние проекта",
        "здоровье проекта",
        "что требует внимания",
        "что требует моего внимания",
        "check the project",
        "check project",
        "project health",
        "project status",
        "requires attention",
        "needs attention",
    )
    if _has_any(text, direct_phrases):
        return True
    return _has_any(text, ("проект", "project")) and _has_any(
        text,
        (
            "проверь",
            "проверить",
            "статус",
            "состояние",
            "health",
            "status",
            "attention",
        ),
    )


def _matches_model_status(text: str) -> bool:
    return _has_any(text, ("модель", "модели", "model", "models")) and _has_any(
        text,
        (
            "покажи",
            "какие",
            "использ",
            "маршрут",
            "роут",
            "status",
            "route",
            "routing",
            "usage",
            "show",
            "which",
        ),
    )


def _matches_urgent_status(text: str) -> bool:
    direct_phrases = (
        "что-то срочное",
        "что нибудь срочное",
        "есть ли срочное",
        "есть ли что-то срочное",
        "есть ли что нибудь срочное",
        "urgent",
        "anything urgent",
        "needs immediate attention",
        "requires immediate attention",
    )
    if _has_any(text, direct_phrases):
        return True
    return _has_any(text, ("сроч", "urgent", "immediate")) and _has_any(
        text,
        ("есть", "что", "anything", "attention"),
    )


def _matches_next_actions(text: str) -> bool:
    direct_phrases = (
        "что делать дальше",
        "что дальше делать",
        "следующий шаг",
        "следующие шаги",
        "куда дальше",
        "what next",
        "what should we do next",
        "next action",
        "next actions",
        "next step",
        "next steps",
    )
    return _has_any(text, direct_phrases)


def _matches_autonomy_readiness(text: str) -> bool:
    return _has_any(
        text,
        (
            "можно ли запускать автономность",
            "можно запускать автономность",
            "готов ли автономный режим",
            "готова ли автономность",
            "can we run autonomy",
            "is autonomy ready",
            "autonomy readiness",
            "ready for autonomy",
            "autonomous readiness",
        ),
    )


def _matches_budget_status(text: str) -> bool:
    return _has_any(
        text,
        (
            "бюджет",
            "расход",
            "стоим",
            "токен",
            "лимит",
            "budget",
            "spend",
            "cost",
            "token",
            "limit",
        ),
    )


def _matches_approval_status(text: str) -> bool:
    return _has_any(
        text,
        (
            "approval",
            "approve",
            "pending approval",
            "одобр",
            "подтвержд",
            "разрешени",
            "ожидает разреш",
        ),
    )
