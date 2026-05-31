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
    "project_health",
    "model_status",
    "budget_status",
    "approval_status",
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
    if _matches_project_health(normalized):
        return OperatorIntent(
            kind="project_health",
            command=":operator-check",
            reason="project health/status wording",
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
            command=":budget-status",
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
