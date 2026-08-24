"""Квотная ошибка понижает провайдера, и это НЕ должно «чиниться».

СВЕРКА С ПОЛЕМ, класс H-47 (docs/audit/HISTORICAL_FAILURE_LEDGER.md) — отказ
переключения GitHub MySQL: автоматика, которая сама причиняет вред.

ЗАМЕР 2026-08-25. Список ВРЕМЕННЫХ маркеров содержал `insufficient_quota`,
`insufficient quota` и `exceeded your current quota`, а список СТОЙКИХ — голую
подстроку `quota`, которая их поглощала. Три маркера не могли сработать
никогда, и при этом комментарий утверждал, что временный класс «переключает, но
не понижает». Слова обещали поведение, которого не было.

ПОЧЕМУ ПОГЛОЩЕНИЕ ВЕРНО, А НЕ ПОДЛЕЖИТ ПОЧИНКЕ. `insufficient_quota` у OpenAI
означает исчерпанный счёт, а не минутный лимит. Понижать за него правильно;
сузить стойкий список значило бы вернуть класс MIR-132 — один пустой баланс,
повторённый 391 раз за четыре дня, потому что ничто не помнило отказ между
процессами.

Тест закрепляет именно эту границу: если квотную ошибку однажды перестанут
считать стойкой, это должно быть отдельным решением с новым замером, а не
побочным следствием «наведения порядка в списках».
"""
from __future__ import annotations

import pytest

import core.model_router as router


def _durable(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in router._KEY_CLASS_TEXT_MARKERS)


def _switches(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in router._SWITCH_KEY_TEXT_MARKERS)


@pytest.mark.parametrize("message", [
    "insufficient_quota: You exceeded your current quota",
    "Error: quota exceeded for this account",
    "RateLimitError: your credit balance is too low",
])
def test_an_account_level_failure_demotes(message: str) -> None:
    assert _durable(message), (
        "исчерпанный счёт перестал понижать провайдера — вернулся класс "
        "MIR-132: один пустой баланс, повторённый 391 раз"
    )
    assert _switches(message), "и переключение обязано остаться"


@pytest.mark.parametrize("message", [
    "RateLimitError: rate limit exceeded, retry in 20s",
    "429 ratelimit",
])
def test_a_rate_limit_switches_but_never_demotes(message: str) -> None:
    """Граница с другой стороны: минутный лимит проходит за секунды.

    Парковать за него здорового провайдера на срок остывания значило бы
    обходить работающего — ровно тот вред, ради которого класс и заведён.
    """
    assert _switches(message)
    assert not _durable(message), (
        "ограничение частоты понижает провайдера — автоматика паркует "
        "здорового и делает хуже, чем было"
    )


def test_no_marker_is_shadowed_into_silence() -> None:
    """Растяжка: маркер, который не может сработать, — это ложное обещание.

    Именно такой список три недели описывал поведение, которого нет.
    """
    durable = [m.lower() for m in router._KEY_CLASS_TEXT_MARKERS]
    shadowed = [
        m for m in router._TRANSIENT_SWITCH_TEXT_MARKERS
        if any(d in m.lower() for d in durable)
    ]
    assert not shadowed, (
        "во временном списке снова есть маркеры, поглощённые стойким — они "
        f"не сработают никогда: {shadowed}"
    )
