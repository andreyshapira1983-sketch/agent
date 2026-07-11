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


def test_budget_status_not_triggered_by_self_build_cost_optimization_request():
    text = (
        "Найди одно минимальное улучшение, которое снизит расход LLM-вызовов "
        "в твоём коде. Ничего не меняй, верни diff-патч и тесты."
    )

    intent = route_operator_intent(text)

    assert intent is None or intent.kind != "budget_status"


def test_self_build_propose_bypasses_operator_shortcuts():
    text = (
        "SELF-BUILD PROPOSE. Inspect code for model routing budget behavior. "
        "Return one file, minimal diff, tests, and risk. Do not read "
        "config/model_registry.json."
    )

    assert route_operator_intent(text) is None


def test_budget_status_not_triggered_by_engineering_token_usage_request():
    text = (
        "Проанализируй свой код и предложи изменение, которое уменьшит "
        "token usage. Верни файл, diff и тесты."
    )

    intent = route_operator_intent(text)

    assert intent is None or intent.kind != "budget_status"


def test_routes_budget_status_when_asking_for_current_limits():
    intent = route_operator_intent("Покажи дневной лимит и расход токенов")

    assert intent is not None
    assert intent.kind == "budget_status"
    assert intent.command == ":operator-budget"


def test_budget_status_not_triggered_by_repair_with_budget_filename():
    # "budget" inside a filename (core/budget_ledger.py) must not trigger budget_status
    intent = route_operator_intent("Запусти self-repair на модуле core/budget_ledger.py")
    assert intent is None or intent.kind != "budget_status"


def test_budget_status_not_triggered_by_repair_keyword():
    intent = route_operator_intent("repair core/budget_ledger.py and show result")
    assert intent is None or intent.kind != "budget_status"


def test_routes_approval_status_phrases():
    intent = route_operator_intent("Есть ли ожидающие approval или разрешения")

    assert intent is not None
    assert intent.kind == "approval_status"
    assert intent.command == ":approval-list all"


def test_approval_status_not_triggered_by_negated_context():
    # "без явного одобрения" means "without approval", not "show approval inbox"
    intent = route_operator_intent(
        'Запусти work session с целью "оптимизировать код" без явного одобрения'
    )
    assert intent is None or intent.kind != "approval_status"


def test_approval_status_not_triggered_by_without_approval_en():
    intent = route_operator_intent("run work session without approval")
    assert intent is None or intent.kind != "approval_status"


def test_approval_status_not_triggered_by_confirmed_facts_phrase():
    # Regression: a bare "подтвержд" stem used to hijack ordinary requests that
    # merely mention "подтверждённые факты" (= confirmed/verified facts) and
    # route them to `:approval-list all` instead of doing the real task.
    samples = [
        "Напиши о себе текст без галлюцинаций с подтверждёнными фактами, файл txt",
        "Составь рассказ о своей архитектуре, используя только подтверждённые факты",
        "Дай подтверждённую версию расчёта",
    ]
    for text in samples:
        intent = route_operator_intent(text)
        assert intent is None or intent.kind != "approval_status", text


def test_approval_status_still_matches_real_inbox_queries():
    # The narrowed matcher must keep routing genuine approval-inbox questions.
    samples = [
        "Есть ли ожидающие approval или разрешения",
        "Покажи очередь одобрений",
        "Что ожидает подтверждения в инбоксе",
        "pending approval list",
    ]
    for text in samples:
        intent = route_operator_intent(text)
        assert intent is not None, text
        assert intent.kind == "approval_status", text
        assert intent.command == ":approval-list all", text


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


def test_bug_note_with_patch_proposal_phrase_does_not_route():
    text = "BUG: plain note mentions patch proposal but is only recording a routing symptom"

    assert route_operator_intent(text) is None


def test_bug_note_with_project_status_words_does_not_route_to_operator_check():
    text = "BUG: project status text is a note, not an operator-check request"

    assert route_operator_intent(text) is None


def test_explicit_remember_bug_note_is_not_operator_routed():
    assert route_operator_intent(":remember bug BUG: project status should be saved") is None


def test_explicit_patch_proposal_command_routes_to_patch_proposal():
    intent = route_operator_intent(":patch-proposal-plan fix operator routing")

    assert intent is not None
    assert intent.kind == "patch_proposal"
    assert intent.command == ":patch-proposal-plan"


def test_does_not_capture_normal_chat():
    assert route_operator_intent("как дела") is None
    assert route_operator_intent("напиши короткое письмо") is None


def test_meta_instruction_negation_does_not_route_to_keyword_shortcut():
    # A rule *about* routing must not trigger the very shortcut it describes.
    samples = [
        "Не маршрутизировать в implementation_plan, если пользователь просит создать заявку",
        "Не вызывай budget_status, когда в тексте есть слово бюджет",
        "Правило: симптом про approval не должен открывать approval inbox",
        "do not route to implementation_plan when the user asks for a plan",
    ]
    for sample in samples:
        assert route_operator_intent(sample) is None


def test_explicit_inbox_task_request_outranks_implementation_plan():
    samples = [
        "Создай заявку в inbox на починку буфера задач",
        "Создай proposed_task для рефакторинга роутера",
        "Запиши в inbox: добавить :task-begin / :task-end",
        "create proposed_task to fix router priority",
    ]
    for sample in samples:
        intent = route_operator_intent(sample)
        assert intent is None or intent.kind != "implementation_plan"


def test_symptom_report_with_budget_and_approval_words_is_not_routed():
    # Real operator bug report: mentions "бюджет"/"approval" only as the subject
    # of a symptom/constraint note, not as a status request.
    text = (
        "симптом 1: audit-запрос ушёл в budget_status из-за слова бюджет\n"
        "симптом 2: follow-up ушёл в approval_status\n"
        "ограничение: не применяй патч"
    )
    assert route_operator_intent(text) is None


def test_routes_best_next_action_phrases_locally():
    ru = route_operator_intent("Выбери одно важнейшее действие прямо сейчас")
    ru2 = route_operator_intent("Что сейчас важнее всего")
    en = route_operator_intent("Pick the single most important action to take now")

    assert ru is not None
    assert ru.kind == "best_next_action"
    assert ru.command == ":best-next-action"
    assert ru2 is not None
    assert ru2.kind == "best_next_action"
    assert en is not None
    assert en.kind == "best_next_action"


def test_best_next_action_does_not_steal_next_actions_list():
    # A LIST of next steps must still go to :next-actions, not :best-next-action.
    intent = route_operator_intent("Что делать дальше")
    assert intent is not None
    assert intent.kind == "next_actions"
    assert intent.command == ":next-actions"


def test_routes_self_task_propose_phrases_locally():
    ru = route_operator_intent("Найди TODO в коде и предложи задачу с падающим тестом")
    en = route_operator_intent("Find a FIXME and propose a coding task with a failing test")

    assert ru is not None
    assert ru.kind == "self_task_proposal"
    assert ru.command == ":self-task-propose"
    assert en is not None
    assert en.kind == "self_task_proposal"


def test_self_task_propose_needs_both_debt_and_propose_signal():
    # Debt marker alone must not route (bare "do we have TODOs?").
    assert route_operator_intent("Есть ли в проекте TODO?") is None
    # Propose signal alone (no code-debt marker) must not route.
    assert route_operator_intent("Предложи тест для калькулятора") is None


def test_self_task_propose_survives_filename_in_text():
    # "TODO in foo.py + предложи тесты" must NOT fall into implementation_plan.
    intent = route_operator_intent("Найди TODO в core/foo.py и предложи тесты")
    assert intent is not None
    assert intent.kind == "self_task_proposal"
    assert intent.command == ":self-task-propose"


def test_routes_architecture_audit_phrases_locally():
    ru = route_operator_intent("Проведи аудит архитектуры и покажи разрывы")
    en = route_operator_intent("Audit the architecture and report the gaps")

    assert ru is not None
    assert ru.kind == "architecture_audit"
    assert ru.command == ":architecture-audit"
    assert en is not None
    assert en.kind == "architecture_audit"


def test_architecture_audit_outranks_project_health_when_both_apply():
    # "проверь архитектуру проекта" satisfies the broad project_health branch
    # (проект + проверь) too; architecture audit must win.
    intent = route_operator_intent("Проверь архитектуру проекта")
    assert intent is not None
    assert intent.kind == "architecture_audit"
    assert intent.command == ":architecture-audit"


def test_plain_project_check_still_routes_to_project_health():
    intent = route_operator_intent("Проверь проект и скажи что требует внимания")
    assert intent is not None
    assert intent.kind == "project_health"


def test_architecture_mention_without_audit_verb_does_not_route():
    # No audit/review verb -> not an architecture audit request.
    assert route_operator_intent("Расскажи про архитектуру этого агента") is None


def test_routes_subagent_proposal_phrases_locally():
    ru = route_operator_intent("Предложи ограниченного субагента для мониторинга почты")
    en = route_operator_intent("Propose a scoped subagent for the ingestion goal")

    assert ru is not None
    assert ru.kind == "subagent_proposal"
    assert ru.command == ":subagent-proposal"
    assert en is not None
    assert en.kind == "subagent_proposal"


def test_subagent_proposal_needs_a_propose_verb():
    # Bare mention / question about a subagent must not route to the proposal.
    assert route_operator_intent("Что такое субагент") is None


def test_subagent_capability_wish_still_routes_to_capability_request():
    # "хочу subagent" is a missing-capability wish (no propose verb), so it must
    # keep going to :capability-request, not :subagent-proposal.
    intent = route_operator_intent("Хочу чтобы ты сам мог запускать subagent")
    assert intent is not None
    assert intent.kind == "capability_request"
    assert intent.command == ":capability-request"


def test_routes_self_build_request_phrases_locally():
    for phrase in (
        "Начни программировать себя",
        "Начни безопасно программировать себя",
        "Начни безопасно программировать себя и передай исправление на проверку",
        "Start programming yourself safely",
        "Запусти self-build",
    ):
        intent = route_operator_intent(phrase)
        assert intent is not None, phrase
        assert intent.kind == "self_build_request", phrase
        assert intent.command == ":self-build-produce", phrase


def test_self_build_request_ignores_questions_and_negations():
    # Descriptions, questions and negations must NOT trigger the producer.
    for phrase in (
        "Расскажи, как агент может программировать себя",
        "Может ли агент программировать себя?",
        "Не начинай программировать себя",
    ):
        assert route_operator_intent(phrase) is None, phrase


def test_self_build_mention_without_start_verb_still_falls_through():
    # A self-build mention with only analysis wording keeps its old behaviour:
    # not routed to the producer (falls through to the planner).
    assert route_operator_intent(
        "проанализируй self-build и предложи улучшение кода"
    ) is None
