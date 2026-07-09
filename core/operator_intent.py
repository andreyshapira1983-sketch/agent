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
    "capability_request",
    "safe_self_check",
    "capability_check",
    "programming_readiness",
    "current_gaps_check",
    "weakness_finder",
    "next_safe_test",
    "project_health",
    "smart_memory_status",
    "model_status",
    "budget_status",
    "approval_status",
    "urgent_status",
    "next_actions",
    "autonomy_readiness",
    "source_review_plan",
    "implementation_plan",
    "patch_proposal",
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
    # Long pasted documents (job listings, articles, log dumps) almost
    # never express an operator intent — they are content for the agent
    # to process. Substring heuristics on tokens like "письм", "нужен",
    # "почт" otherwise misclassify them as capability/email requests.
    # Real intents fit comfortably under this size.
    if len(normalized) > 600:
        return None
    # P0 operator routing guard: negations and meta-instructions describe a
    # rule *about* routing ("не маршрутизируй в implementation_plan", "если
    # пользователь просит ...", "симптом ...") rather than asking for a status
    # action. Keyword shortcuts must not fire on them — hand the text to the
    # normal planner instead.
    if _looks_like_meta_instruction(normalized):
        return None
    if _looks_like_plain_bug_note(normalized):
        return None
    if _looks_like_explicit_non_routing_command(normalized):
        return None
    if _looks_like_self_build_request(normalized):
        return None
    # P0 explicit inbox / proposed_task intent: creating an approval-inbox
    # request must outrank implementation/source-review keyword matching, which
    # otherwise hijacks "создай заявку в inbox" into a planning digest.
    if _matches_inbox_task_request(normalized):
        return None
    if _looks_like_shell_command(normalized):
        return OperatorIntent(
            kind="shell_command_hint",
            command="shell-command-hint",
            reason="shell/powershell command wording",
        )
    if _matches_capability_request(normalized):
        return OperatorIntent(
            kind="capability_request",
            command=":capability-request",
            reason="missing capability / connector proposal wording",
        )
    if _matches_patch_proposal(normalized):
        return OperatorIntent(
            kind="patch_proposal",
            command=":patch-proposal-plan",
            reason="patch proposal wording",
        )
    if _matches_source_review(normalized):
        return OperatorIntent(
            kind="source_review_plan",
            command=":source-review-plan",
            reason="implementation/source-review planning wording",
        )
    if _matches_implementation_plan(normalized):
        return OperatorIntent(
            kind="implementation_plan",
            command=":implementation-plan",
            reason="implementation planning wording",
        )
    if _explicit_documentation_requested(normalized):
        return None
    if _matches_safe_self_check(normalized):
        return OperatorIntent(
            kind="safe_self_check",
            command=":operator-check",
            reason="safe self-check wording",
        )
    if _matches_programming_readiness(normalized):
        return OperatorIntent(
            kind="programming_readiness",
            command=":coding-readiness",
            reason="safe programming readiness wording",
        )
    if _matches_capability_check(normalized):
        return OperatorIntent(
            kind="capability_check",
            command="operator-capabilities",
            reason="capability/status wording",
        )
    if _matches_current_gaps_check(normalized):
        return OperatorIntent(
            kind="current_gaps_check",
            command="operator-gaps",
            reason="current gaps wording",
        )
    if _matches_weakness_finder(normalized):
        return OperatorIntent(
            kind="weakness_finder",
            command="operator-weaknesses",
            reason="live weakness wording",
        )
    if _matches_next_safe_test(normalized):
        return OperatorIntent(
            kind="next_safe_test",
            command="operator-next-safe-test",
            reason="next safe test wording",
        )
    if _matches_project_health(normalized):
        return OperatorIntent(
            kind="project_health",
            command=":operator-check",
            reason="project health/status wording",
        )
    if _matches_smart_memory_status(normalized):
        return OperatorIntent(
            kind="smart_memory_status",
            command=":smart-memory",
            reason="memory status wording",
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


def _looks_like_meta_instruction(text: str) -> bool:
    """Detect text that states a *rule about* routing rather than a request.

    These phrasings (negations, conditionals, requirement/constraint language,
    symptom reports) must not trigger keyword-based operator shortcuts, because
    the matching word (budget / approval / implementation / plan) only appears
    as the subject of an instruction, not as something the operator is asking
    the agent to do right now.
    """
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
    """Detect an explicit request to create an approval-inbox proposed_task.

    Such requests must outrank implementation/source-review planning matchers
    so the agent actually files the task instead of returning a planning digest.
    """
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


def _matches_approval_status(text: str) -> bool:
    # Don't match negated approval context — user saying "without approval" /
    # "без одобрения" is asking to bypass it, not to inspect the inbox.
    if _has_any(text, ("без одобр", "without approval", "no approval", "без явного")):
        return False
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
