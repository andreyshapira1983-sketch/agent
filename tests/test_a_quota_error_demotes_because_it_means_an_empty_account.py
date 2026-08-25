"""Квотная ошибка понижает провайдера, и это НЕ должно «чиниться».

Замер, отвергнутые варианты и границы: H-47 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, MIR-132 в docs/audit/MASTER_ISSUE_REGISTRY.md.
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
