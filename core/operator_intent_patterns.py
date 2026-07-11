from __future__ import annotations


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _looks_like_meta_instruction(text: str) -> bool:
    meta_markers = (
        "не маршрутизир",
        "не маршрутизаци",
        "не роути",
        "не routи",
        "не вызывай",
        "не вызови",
        "не применяй",
        "не применить",
        "не запускай",
        "не используй",
        "не должен",
        "не нужно маршрут",
        "если пользователь просит",
        "если пользователь пишет",
        "если текст содержит",
        "должен ",
        "должна ",
        "должно ",
        "правило:",
        "правило ",
        "симптом",
        "ограничение",
        "do not route",
        "don't route",
        "do not call",
        "don't call",
        "should not route",
        "must not route",
        "if the user asks",
        "if the user requests",
        "rule:",
    )
    return _has_any(text, meta_markers)


def _looks_like_plain_bug_note(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("bug:")


def _looks_like_explicit_non_routing_command(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(":") and not (
        stripped.startswith(":patch-proposal-plan") or stripped.startswith(":patch-plan")
    )


def _looks_like_self_build_request(text: str) -> bool:
    return _has_any(
        text,
        ("self-build", "selfbuild", "self build", "самостро"),
    ) and _has_any(
        text,
        ("propose", "inspect", "найди", "проанализ", "улучш", "код", "code", "diff"),
    )


def _matches_inbox_task_request(text: str) -> bool:
    inbox_markers = (
        "создай заявку в inbox",
        "создать заявку в inbox",
        "создай заявку в инбокс",
        "заявку в inbox",
        "заявку в инбокс",
        "создай proposed_task",
        "создать proposed_task",
        "создай proposed task",
        "запиши в inbox",
        "запиши в инбокс",
        "добавь в inbox",
        "добавь в инбокс",
        "create proposed_task",
        "create inbox task",
        "add to inbox",
    )
    return _has_any(text, inbox_markers)


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


def _explicit_documentation_requested(text: str) -> bool:
    return _has_any(
        text,
        (
            "по readme",
            "из readme",
            "прочитай readme",
            "что написано в readme",
            "по документации",
            "из документации",
            "прочитай документац",
            "по документу",
            "из документа",
        ),
    )


def _matches_patch_proposal(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith(":patch-proposal-plan") or stripped.startswith(":patch-plan"):
        return True
    return _has_any(
        text,
        (
            "propose a patch",
            "propose patch",
            "propose a diff",
            "create a patch",
            "create patch proposal",
            "draft a patch",
            "draft patch proposal",
            "make a patch",
            "make patch proposal",
            "prepare a patch",
            "prepare patch proposal",
            "produce a patch",
            "produce patch proposal",
            "write a patch",
            "write patch proposal",
            "предложи patch",
            "предложи патч",
            "составь patch",
            "составь патч",
            "подготовь patch",
            "подготовь патч",
            "создай patch",
            "создай патч",
        ),
    )


def _matches_capability_request(text: str) -> bool:
    explicit_capability_terms = (
        "capability request",
        "capability proposal",
        "connector proposal",
        "подключи",
        "подключить",
        "подключение",
        "дай доступ",
        "нужен доступ",
        "нужно подключ",
        "разрешить доступ",
        "разреши доступ",
    )
    autonomous_need_terms = (
        "хочу чтобы ты сам",
        "чтобы ты сам",
        "сам сообщал",
        "сам сообщай",
        "сам уведом",
        "когда нужно решение",
        "уведомляй меня",
        "сообщай мне",
        "следи за upwork",
        "следи за почт",
        "следи за письм",
        "мониторь upwork",
        "мониторь почт",
        "monitor upwork",
        "monitor email",
    )
    concrete_capabilities = (
        "telegram",
        "телеграм",
        "email",
        "e-mail",
        "почту",
        "почты",
        "почтой",
        "письма",
        "письмо",
        "gmail",
        "upwork",
        "апворк",
        "long work",
        "долгую сессию",
        "подагент",
        "subagent",
        "модель выше",
        "premium model",
        "persistent memory",
        "записывал в память",
    )
    if _has_any(text, explicit_capability_terms):
        return True
    if _has_any(text, autonomous_need_terms):
        return True
    return _has_any(text, concrete_capabilities) and _has_any(
        text,
        (
            "нужен",
            "нужно",
            "хочу",
            "разреш",
            "доступ",
            "подключ",
            "следи",
            "монитор",
            "уведом",
            "сообщ",
            "сам",
        ),
    )


def _matches_source_review(text: str) -> bool:
    source_review_terms = (
        "сравни загруженные источники",
        "сравнить загруженные источники",
        "сравни источники",
        "сравнить источники",
        "source review",
        "review loaded sources",
        "file comparison",
        "сравни файлы",
        "сравнить файлы",
    )
    filename_markers = (".py", ".md", ".txt", "\\", "/")
    if _has_any(text, source_review_terms):
        return True
    return _has_any(text, filename_markers) and _has_any(
        text,
        ("сравни", "сравнить", "review", "compare"),
    )


def _matches_implementation_plan(text: str) -> bool:
    planning_terms = (
        "implementation plan",
        "план реализации",
        "составь точный план",
        "точный план реализации",
        "какие файлы менять",
        "какие тесты добавить",
        "operator task layer",
    )
    filename_markers = (".py", ".md", ".txt", "\\", "/")
    if _has_any(text, planning_terms):
        return True
    return _has_any(text, filename_markers) and _has_any(
        text,
        (
            "план",
            "реализац",
            "implementation",
            "менять",
            "тесты",
        ),
    )


def _matches_safe_self_check(text: str) -> bool:
    return _has_any(
        text,
        (
            "начни безопасную проверку себя",
            "проверь себя безопасно",
            "проведи безопасную самопроверку",
            "сделай самопроверку",
            "начни проверку себя",
            "безопасная самопроверка",
            "самопроверку себя",
            "safe self check",
            "safe self-check",
        ),
    )


def _matches_capability_check(text: str) -> bool:
    return _has_any(
        text,
        (
            "проверь свои возможности",
            "что ты можешь делать",
            "какие у тебя возможности",
            "покажи свои способности",
            "твои возможности",
            "свои возможности",
            "capability check",
            "capabilities",
        ),
    )


def _matches_programming_readiness(text: str) -> bool:
    readiness_terms = (
        "готов к безопасной программной задаче",
        "готов к программной задаче",
        "готов к задаче по коду",
        "готовность к программной задаче",
        "готовность к coding",
        "coding readiness",
        "programming readiness",
        "safe coding task",
        "safe programming task",
    )
    if _has_any(text, readiness_terms):
        return True
    return _has_any(
        text,
        ("готов", "готовность", "readiness", "ready"),
    ) and _has_any(
        text,
        ("код", "программ", "coding", "programming", "patch", "тест"),
    )


def _matches_current_gaps_check(text: str) -> bool:
    return _has_any(
        text,
        (
            "посмотри, что у тебя сейчас не готово",
            "что у тебя сейчас не готово",
            "что сейчас не готово",
            "какие gaps остались",
            "какие gap остались",
            "какие гэпы остались",
            "что отсутствует",
            "current gaps",
            "remaining gaps",
        ),
    )


def _matches_weakness_finder(text: str) -> bool:
    return _has_any(
        text,
        (
            "найди слабое место в своей системе",
            "слабое место в своей системе",
            "где слабое место",
            "что самое опасное сейчас",
            "где риск",
            "weakness",
            "weak spot",
            "highest risk",
        ),
    )


def _matches_next_safe_test(text: str) -> bool:
    return _has_any(
        text,
        (
            "скажи, какой безопасный тест сделать следующим",
            "какой безопасный тест сделать следующим",
            "что проверить дальше безопасно",
            "какой следующий безопасный тест",
            "next safe test",
            "safe test next",
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


def _matches_best_next_action(text: str) -> bool:
    # The SINGLE most important action to take right now. Deliberately narrow
    # and kept distinct from _matches_next_actions (a LIST of next steps) by
    # requiring singular / top-priority wording. Must be routed BEFORE
    # _matches_next_actions because the English form "most important next
    # action" contains the "next action" trigger that matcher fires on.
    return _has_any(
        text,
        (
            "важнее всего",
            "важнейшее действие",
            "самое важное действие",
            "самое приоритетное действие",
            "одно важнейшее",
            "одно самое важное",
            "одно самое приоритетное",
            "какое одно действие",
            "приоритет номер один",
            "single most important",
            "most important next action",
            "most important action",
            "most important thing to do",
            "one most important",
            "highest priority action",
            "top priority action",
        ),
    )


def _matches_self_task_propose(text: str) -> bool:
    # Stage-A coding-task producer: take a real TODO/FIXME and propose ONE
    # coding task + failing acceptance test for human approval. Requires BOTH a
    # code-debt marker AND a propose-task/test signal so it never steals plain
    # "find X" traffic or a bare bug note. Routed BEFORE the plan/source-review
    # matchers so a "TODO in foo.py + предложи тесты" phrasing does not fall
    # into the generic implementation-plan (.py + тесты) branch.
    debt = _has_any(
        text,
        ("todo", "fixme", "tech debt", "техдолг", "технический долг"),
    )
    propose = _has_any(
        text,
        (
            "предложи задач",
            "предложи один",
            "предложи тест",
            "предложи acceptance",
            "предложи падающий",
            "создай задач",
            "создай тест",
            "падающий тест",
            "failing test",
            "acceptance test",
            "propose a task",
            "propose one",
            "propose a test",
            "coding task",
            "self-task",
        ),
    )
    return debt and propose


def _matches_architecture_audit(text: str) -> bool:
    # Read-only architecture audit (layers / multi-agent gaps). Requires an
    # architecture term AND an audit/review verb so it never fires on a plain
    # "проверь проект" (project health) or a mere mention of architecture.
    # Must be routed BEFORE _matches_project_health, because
    # "проверь архитектуру проекта" also satisfies that broad project branch.
    architecture = _has_any(text, ("архитектур", "architecture", "architectural"))
    audit_verb = _has_any(
        text,
        (
            "аудит",
            "audit",
            "обзор",
            "review",
            "оцени",
            "оценить",
            "оценка",
            "проверь",
            "проверить",
            "проверка",
            "инспек",
            "inspect",
            "проанализируй",
            "анализ",
            "analyze",
            "analyse",
        ),
    )
    return architecture and audit_verb


def _matches_subagent_proposal(text: str) -> bool:
    # Scoped autonomous-subagent proposal (SubagentProposal contract from a
    # goal). Distinct from capability_request (which treats "subagent" as a
    # missing capability): this needs an explicit PROPOSE/DESIGN verb next to
    # the subagent term. Routed BEFORE capability_request so "предложи
    # ограниченного субагента" wins over any capability overlap.
    subagent = _has_any(
        text,
        ("субагент", "суб-агент", "подагент", "под-агент", "subagent", "sub-agent"),
    )
    propose_verb = _has_any(
        text,
        (
            "предлож",
            "propose",
            "сформируй",
            "сформулируй",
            "подготовь",
            "составь",
            "спроектируй",
            "design",
            "draft",
            "инициатив",
            "initiative",
        ),
    )
    return subagent and propose_verb

def _matches_autonomy_readiness(text: str) -> bool:
    return _has_any(
        text,
        (
            "можно ли запускать автономность",
            "можно запускать автономность",
            "готов ли автономный режим",
            "готова ли автономность",
            "готов к автономной работе",
            "насколько ты готов к автономной",
            "can we run autonomy",
            "is autonomy ready",
            "autonomy readiness",
            "ready for autonomy",
            "autonomous readiness",
        ),
    )


def _matches_budget_status(text: str) -> bool:
    if _looks_like_engineering_change_request(text):
        return False
    if _is_explicit_budget_status_command(text):
        return True
    if not _has_any(
        text,
        (
            "бюджет",
            "расход",
            "стоим",
            "токен",
            "лимит",
            "llm-выз",
            "llm выз",
            "llm_call",
            "llm call",
            "budget",
            "spend",
            "spent",
            "cost",
            "token",
            "limit",
        ),
    ):
        return False
    return _has_any(
        text,
        (
            "сколько",
            "какой",
            "какая",
            "какие",
            "покажи",
            "показать",
            "статус",
            "остат",
            "израсход",
            "потра",
            "текущ",
            "сейчас",
            "сегодня",
            "how much",
            "what is",
            "show",
            "status",
            "remaining",
            "current",
            "today",
            "used",
            "usage",
        ),
    )


def _is_explicit_budget_status_command(text: str) -> bool:
    head = text.strip().split(maxsplit=1)[0]
    return head in (
        ":operator-budget",
        ":budget-digest",
        ":budget-status",
        ":budget-config",
        ":budget-limits",
        ":budget-window-status",
        ":budget-windows",
        ":budget-ledger",
        ":model-usage",
        ":usage-models",
    )


def _looks_like_engineering_change_request(text: str) -> bool:
    engineering_terms = (
        "self-build",
        "self build",
        "самостро",
        "код",
        "code",
        ".py",
        "файл",
        "file",
        "модул",
        "module",
        "patch",
        "diff",
        "патч",
        "тест",
        "test",
        "улучш",
        "изменен",
        "изменить",
        "изменение",
        "change",
        "рефактор",
        "llm-выз",
        "llm выз",
        "llm_call",
        "llm call",
        "короткие простые вопросы",
    )
    change_terms = (
        "найди",
        "проанализ",
        "предлож",
        "составь",
        "верни",
        "ничего не меняй",
        "сниз",
        "уменьш",
        "оптимиз",
        "исправ",
        "почин",
        "analy",
        "find",
        "propose",
        "return",
        "improve",
        "reduce",
        "lower",
        "optimi",
        "fix",
        "repair",
    )
    return _has_any(text, engineering_terms) and _has_any(text, change_terms)


def _matches_smart_memory_status(text: str) -> bool:
    if _has_any(
        text,
        (
            "smart memory",
            "experience memory",
            "episodic memory",
            "procedural memory",
            "consolidation memory",
            "умная память",
            "эпизодическая память",
            "процедурная память",
            "консолидация памяти",
        ),
    ):
        return True
    return _has_any(text, ("память", "memory")) and _has_any(
        text,
        (
            "какая",
            "покажи",
            "статус",
            "существует",
            "status",
            "show",
        ),
    )


# Words that unambiguously refer to the approval inbox/queue on their own.
_APPROVAL_STRONG = (
    "approval",
    "approve",
    "pending approval",
    "одобр",  # одобрение / одобрить / одобрения — approval-specific in RU
    "ожидает разреш",
    "апрув",
)

# Stems that *can* mean "approval" but are ambiguous in everyday phrasing:
#   "подтверждённые факты"  = confirmed / verified facts (NOT an approval query)
#   "разрешение экрана"     = screen resolution
#   "разрешение конфликта"  = conflict resolution
# They only signal an approval-inbox query when an approval/inbox context word
# is also present. Bug: a bare "подтвержд" hijacked normal requests such as
# "напиши текст с подтверждёнными фактами" into `:approval-list all`.
_APPROVAL_AMBIGUOUS = (
    "подтвержд",
    "разрешени",
)

_APPROVAL_CONTEXT = (
    "approval",
    "approve",
    "одобр",
    "инбокс",
    "inbox",
    "очеред",  # очередь
    "queue",
    "список",
    "list",
    "заявк",  # заявка
    "запрос",
    "pending",
    "ожида",  # ожидает / ожидающие
    "runtime",
)


def _matches_approval_status(text: str) -> bool:
    if _has_any(text, ("без одобр", "without approval", "no approval", "без явного")):
        return False
    if _has_any(text, _APPROVAL_STRONG):
        return True
    # Ambiguous stems ("подтвержд", "разрешени") only count when paired with an
    # approval/inbox context word — otherwise phrases like "подтверждённые
    # факты" must NOT be routed to the approval inbox.
    if _has_any(text, _APPROVAL_AMBIGUOUS) and _has_any(text, _APPROVAL_CONTEXT):
        return True
    return False
