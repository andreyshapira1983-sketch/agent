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


def test_routes_safe_self_check_phrases_locally():
    samples = [
        "Начни безопасную проверку себя",
        "Проверь себя безопасно",
        "Проведи безопасную самопроверку",
        "Сделай самопроверку",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "safe_self_check"
        assert intent.command == ":operator-check"


def test_routes_capability_check_phrases_locally():
    intent = route_operator_intent("Проверь свои возможности")

    assert intent is not None
    assert intent.kind == "capability_check"
    assert intent.command == "operator-capabilities"


def test_routes_capability_request_phrases_locally():
    samples = [
        "Хочу, чтобы ты сам сообщал мне, когда нужно решение",
        "Следи за важными письмами и попроси доступ если нужно",
        "Нужно подключить Upwork мониторинг",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "capability_request"
        assert intent.command == ":capability-request"


def test_routes_programming_readiness_phrases_locally():
    samples = [
        "Проверь, насколько ты готов к безопасной программной задаче",
        "Покажи programming readiness",
        "Ты готов к задаче по коду?",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "programming_readiness"
        assert intent.command == ":coding-readiness"


def test_routes_current_gaps_and_weakness_phrases_locally():
    gaps = route_operator_intent("Посмотри, что у тебя сейчас не готово")
    weakness = route_operator_intent("Найди слабое место в своей системе")

    assert gaps is not None
    assert gaps.kind == "current_gaps_check"
    assert weakness is not None
    assert weakness.kind == "weakness_finder"


def test_routes_next_safe_test_phrase_locally():
    intent = route_operator_intent("Скажи, какой безопасный тест сделать следующим")

    assert intent is not None
    assert intent.kind == "next_safe_test"
    assert intent.command == "operator-next-safe-test"


def test_explicit_readme_or_docs_request_is_not_captured_by_live_operator_router():
    assert route_operator_intent("Расскажи по README, что ты умеешь") is None
    assert route_operator_intent("Проверь свои возможности по документации") is None


def test_routes_model_status_phrases():
    intent = route_operator_intent("Покажи какие модели сейчас используются")

    assert intent is not None
    assert intent.kind == "model_status"
    assert intent.command == ":models"


def test_routes_smart_memory_status_phrases():
    intent = route_operator_intent("Какая память у меня существует")

    assert intent is not None
    assert intent.kind == "smart_memory_status"
    assert intent.command == ":smart-memory"


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


def test_source_review_requests_route_to_source_review_plan():
    intent = route_operator_intent(
        "Сравни загруженные источники operator_task_layer_request.md, main.py и core/operator_intent.py"
    )

    assert intent is not None
    assert intent.kind == "source_review_plan"
    assert intent.command == ":source-review-plan"


def test_implementation_planning_requests_route_to_implementation_plan():
    samples = [
        "Составь точный план реализации Operator Task Layer",
        "Проверь .\\operator_task_layer_request.md, .\\main.py и .\\core\\operator_intent.py и скажи какие файлы менять",
    ]

    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is not None
        assert intent.kind == "implementation_plan"
        assert intent.command == ":implementation-plan"


def test_patch_proposal_requests_route_to_patch_proposal():
    intent = route_operator_intent("Составь patch proposal для исправления operator routing")

    assert intent is not None
    assert intent.kind == "patch_proposal"
    assert intent.command == ":patch-proposal-plan"


def test_does_not_capture_normal_chat():
    assert route_operator_intent("как дела") is None
    assert route_operator_intent("напиши короткое письмо") is None
