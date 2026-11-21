from __future__ import annotations

from core.operator_intent import route_operator_intent


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


def test_routes_model_status_phrases():
    intent = route_operator_intent("Покажи какие модели сейчас используются")

    assert intent is not None
    assert intent.kind == "model_status"
    assert intent.command == ":models"


def test_routes_model_status_genitive_roles_phrase():
    """Russian genitive 'моделей' must still hit :models, not web_search."""
    intent = route_operator_intent("какие роли моделей сейчас активны? Ответь коротко.")

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


def test_self_build_propose_bypasses_operator_shortcuts():
    text = (
        "SELF-BUILD PROPOSE. Inspect code for model routing budget behavior. "
        "Return one file, minimal diff, tests, and risk. Do not read "
        "config/model_registry.json."
    )

    assert route_operator_intent(text) is None


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


def test_routes_subagent_proposal_phrases_locally():
    ru = route_operator_intent("Предложи ограниченного субагента для мониторинга почты")
    en = route_operator_intent("Propose a scoped subagent for the ingestion goal")

    assert ru is not None
    assert ru.kind == "subagent_proposal"
    assert ru.command == ":subagent-proposal"
    assert en is not None
    assert en.kind == "subagent_proposal"


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
        "Расскажи, как ты программируешь себя",
        "Можешь ли ты программировать себя?",
        "Как работает self-build?",
        "Не начинай программировать себя",
        "Не запускай self-build",
    ):
        assert route_operator_intent(phrase) is None, phrase


def test_self_build_request_ignores_rule_and_matcher_discussion():
    # Meta / rule / test-phrase / matcher-authoring text that merely QUOTES the
    # trigger phrase must not route to the producer.
    for phrase in (
        'правило: если пользователь пишет "начни программировать себя", не маршрутизируй',
        'Обсудим тестовую фразу "начни программировать себя"',
        'Просьба добавить matcher для формулировки "начни программировать себя"',
    ):
        assert route_operator_intent(phrase) is None, phrase


def test_self_build_mention_without_start_verb_still_falls_through():
    # A self-build mention with only analysis wording keeps its old behaviour:
    # not routed to the producer (falls through to the planner).
    assert route_operator_intent(
        "проанализируй self-build и предложи улучшение кода"
    ) is None


# ── capability questions with a filler word in the middle ────────────────────
#
# Measured from a live session (2026-07-26): the operator typed "что ты уже
# умеешь". Literal substring matching missed it, the turn went to the LLM, and
# the answer was generic model boilerplate ("I can translate texts") instead of
# the factual capability report the local handler produces. Two model calls to
# say nothing about THIS agent — and the verifier scored the result 0.8%.


def test_a_question_sent_to_the_outside_world_is_never_hijacked():
    """Живой перехват 2026-08-16 (trace hypothesis_probe2): вопрос «Зайди в
    интернет и прочитай про CrewAI... архитектурную идею... ПРОВЕРКА:...»
    угнан в :architecture-audit — «архитектур*» + «провер*» совпали, и чтение
    внешнего мира не случилось вовсе. Сообщение, посылающее агента НАРУЖУ
    (интернет/сайт/URL), не бывает внутренней командой — оператор не обязан
    знать про :task-begin, чтобы задать вопрос.
    """
    hijacked = (
        "Зайди в интернет и прочитай, что такое CrewAI (мультиагентный "
        "фреймворк). Извлеки его главную архитектурную идею и примерь к "
        "СВОЕЙ системе. В конце блок: «ПРОВЕРКА: <какой замер решит>»."
    )
    assert route_operator_intent(hijacked) is None

    for text in (
        "Прочитай https://example.com/agents и сравни их аудит архитектуры с твоим",
        "Найди в вебе обзор architecture review практик и оцени применимость",
    ):
        intent = route_operator_intent(text)
        assert intent is None or intent.kind != "architecture_audit", text


def test_a_genuine_audit_ask_still_routes():
    """Улов не отдан: настоящая просьба об аудите идёт куда шла."""
    intent = route_operator_intent("Проведи аудит архитектуры и покажи разрывы")
    assert intent is not None and intent.kind == "architecture_audit"
