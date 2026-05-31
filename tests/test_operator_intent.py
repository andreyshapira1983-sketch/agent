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
    assert intent.command == ":operator-budget"


def test_routes_approval_status_phrases():
    intent = route_operator_intent("Есть ли ожидающие approval или разрешения")

    assert intent is not None
    assert intent.kind == "approval_status"
    assert intent.command == ":approval-list all"


def test_routes_urgent_status_phrases():
    intent = route_operator_intent("Есть ли что-то срочное")

    assert intent is not None
    assert intent.kind == "urgent_status"
    assert intent.command == ":urgent-status"


def test_routes_next_actions_phrases():
    intent = route_operator_intent("Что делать дальше")

    assert intent is not None
    assert intent.kind == "next_actions"
    assert intent.command == ":next-actions"


def test_routes_autonomy_readiness_phrases():
    intent = route_operator_intent("Можно ли запускать автономность")

    assert intent is not None
    assert intent.kind == "autonomy_readiness"
    assert intent.command == ":autonomy-readiness"


def test_shell_commands_are_not_misrouted_to_operator_status():
    samples = [
        r"py -3 .\main.py --auto-approve deny --file .\x.md",
        r"Test-Path .\docs\x.md",
        r"Get-ChildItem .\docs",
        "git status",
        "pytest tests/test_operator_intent.py",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "shell_command_hint"
        assert intent.command == "shell-command-hint"


def test_implementation_planning_requests_route_to_source_review_plan():
    samples = [
        "Составь точный план реализации Operator Task Layer",
        "Сравни загруженные источники operator_task_layer_request.md, main.py и core/operator_intent.py",
        "Проверь .\\operator_task_layer_request.md, .\\main.py и .\\core\\operator_intent.py и скажи какие файлы менять",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "source_review_plan"
        assert intent.command == ":source-review-plan"


def test_does_not_capture_normal_chat():
    assert route_operator_intent("как дела") is None
    assert route_operator_intent("напиши короткое письмо") is None
