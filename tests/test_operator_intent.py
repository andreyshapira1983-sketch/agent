from __future__ import annotations

from core.operator_intent import route_operator_intent


def test_routes_project_health_phrases_without_llm():
    ru = route_operator_intent("Проверь проект и скажи что требует внимания")
    en = route_operator_intent("Check the project and tell me what needs attention")

    assert ru is not None
    assert ru.kind == "project_health"
    assert ru.command == ":operator-check"
    assert en is not None
    assert en.kind == "project_health"


def test_routes_model_status_phrases():
    intent = route_operator_intent("Покажи какие модели сейчас используются")

    assert intent is not None
    assert intent.kind == "model_status"
    assert intent.command == ":models"


def test_routes_budget_status_phrases():
    intent = route_operator_intent("Сколько потрачено токенов и какой бюджет")

    assert intent is not None
    assert intent.kind == "budget_status"
    assert intent.command == ":budget-status"


def test_routes_approval_status_phrases():
    intent = route_operator_intent("Есть ли ожидающие approval или разрешения")

    assert intent is not None
    assert intent.kind == "approval_status"
    assert intent.command == ":approval-list all"


def test_does_not_capture_normal_chat():
    assert route_operator_intent("как дела") is None
    assert route_operator_intent("напиши короткое письмо") is None
