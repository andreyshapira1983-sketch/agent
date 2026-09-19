"""Остаток секрета — не знание, даже когда сам секрет уже вырезан.

ЗАМЕР 2026-08-10 по `data/persistent_memory.jsonl`. Прогон прочитал `.env`,
ядро классифицировало источник как `class=secret` и подняло `secret_detected
count=6`, вырезав значения. После этого конвейер знаний записал в ДОЛГОВЕЧНУЮ
семантическую память строки вида `TELEGRAM=[REDACTED:telegram-bot-token]`,
`HF_TOKEN=[REDACTED:huggingface-token]`, с тегами `fact, knowledge,
source-backed`.

Проверка на секрет у политики записи есть — `contains_secret(text)`. Она не
сработала ровно потому, что редактирование УЖЕ прошло: под шаблон ключа
`[REDACTED:...]` не подходит. Вердикт источника («это секретный файл») до
политики не доезжает, а текстовая проверка видит безобидную строку.

Что записывается: имена переменных окружения, их наличие и назначение — карта
секретов установки, живущая дольше прогона. Само значение не утекает; утекает
инвентарь. Знанием он не является ни в каком случае.
"""
from __future__ import annotations

import pytest

from core.knowledge_pipeline import KnowledgeWritePolicy


class _Claim:
    """Минимальное утверждение в форме, которую политика умеет судить."""

    def __init__(self, text: str) -> None:
        self.id = "claim_x"
        self.text = text
        self.source_id = "file:.env"
        self.status = "extracted"
        self.confidence = 0.85


class _Source:
    """Зарегистрированный источник с доверием выше порога политики."""

    def __init__(self, source_id: str = "file:.env") -> None:
        self.id = source_id
        self.trust_level = 0.85
        self.trust = 0.85
        self.type = "file"
        self.title = source_id
        self.locator = source_id.split(":", 1)[-1]


@pytest.fixture
def policy() -> KnowledgeWritePolicy:
    return KnowledgeWritePolicy()


@pytest.mark.parametrize("text", [
    "TELEGRAM=[REDACTED:telegram-bot-token]",
    "HF_TOKEN=[REDACTED:huggingface-token]",
    "ANTHROPIC_API_KEY=[REDACTED:anthropic-key]",
    "Ключ [REDACTED:openai-key] используется провайдером openai",
])
def test_redacted_residue_is_refused(policy: KnowledgeWritePolicy, text: str) -> None:
    """ГЛАВНОЕ: ровно те строки, что лежат в постоянной памяти."""
    decision = policy.decide(_Claim(text), source=_Source())
    assert decision.decision == "reject", (
        f"остаток секрета принят в долговечное знание: {text!r}"
    )
    assert any("redact" in r.lower() or "секрет" in r.lower() or "secret" in r.lower()
               for r in decision.reasons), decision.reasons


def test_an_ordinary_claim_still_passes(policy: KnowledgeWritePolicy) -> None:
    """ПРЕДОХРАНИТЕЛЬ: политика не заклинена в положении «отказать»."""
    claim = _Claim("Модуль core/loop.py реализует цикл наблюдения и ответа.")
    claim.source_id = "file:docs/CODE_NOTES.md"
    decision = policy.decide(claim, source=_Source("file:docs/CODE_NOTES.md"))
    assert decision.decision != "reject", decision.reasons


def test_the_word_redacted_alone_is_not_enough(policy: KnowledgeWritePolicy) -> None:
    """Отказ по МАРКЕРУ, а не по теме: текст о редактировании — не секрет.

    Иначе документация про саму редактуру перестала бы быть знанием, и
    починка съела бы область, ради которой существует.
    """
    claim = _Claim("Функция redact заменяет секреты токеном-заполнителем.")
    claim.source_id = "file:docs/CODE_NOTES.md"
    decision = policy.decide(claim, source=_Source("file:docs/CODE_NOTES.md"))
    assert decision.decision != "reject", decision.reasons


def test_a_live_secret_is_still_refused(policy: KnowledgeWritePolicy) -> None:
    """Прежняя дорога цела: неотредактированный секрет отвергается как раньше."""
    claim = _Claim("OPENAI_API_KEY=sk-proj-" + "a" * 40)
    decision = policy.decide(claim, source=_Source())
    assert decision.decision == "reject"
