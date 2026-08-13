"""Вопрос без темы нельзя обвинить в непокрытой теме.

ЗАМЕР 2026-08-13. «Скажи что-нибудь умное» → ответ по делу → хвост оператору:
«Соответствие вопросу: 0.00 — ответ может отвечать не на заданный вопрос».
Механика: токенизатор рвал «что-нибудь» по дефису, обломок «нибудь» сходил за
содержательное слово; «умное» — описание ЖЕЛАЕМОГО ОТВЕТА, а не тема. Покрытие
слов измеряет тематические вопросы; у бестемного императива темы нет — значит,
измерения не было, и честный отчёт об этом «не мерили», а не ноль.

Это вторая форма класса, уже пойманного 2026-08-10 на разных алфавитах: замер,
который не был применим, отчитан как замер. Чинится там же — применимостью,
которая спрашивается ДО значения, — и структурно (слот после неопределённого
местоимения), а не списком прилагательных под каждую фразу оператора.
"""
from __future__ import annotations

import pytest

from core.confidence_vector import compute_vector, relevance_score

# Дословный вопрос и характерное начало ответа из продакшен-трассы
# trace_da0f132bd8e203a77ae5870fdbb39a90 (2026-08-13).
_TRACE_QUESTION = "скажи что-нибудь умное"
_TRACE_ANSWER = (
    "Хорошая стратегия — начать с проблемы и метрики, затем ограничить "
    "пространство решений (время, бюджет, риски) и только после этого "
    "выбирать конкретные инструменты; так вы уменьшаете вероятность "
    "«оптимизации ради оптимизации»."
)


class _Chunk:
    verdict = "verified"


class _Report:
    """Достаточная заглушка отчёта: осям улик и связности здесь всё равно."""
    total_chunks = 4
    verified_chunks = 4
    unverified_chunks = 0
    cited_but_unmatched_chunks = 0
    self_declared_chunks = 0
    chain_was_empty = False
    fully_unverified = False
    chunks: tuple = (_Chunk(), _Chunk(), _Chunk(), _Chunk())
    structural_chunks = 0
    topic_supported_but_claim_unverified_chunks = 0
    subagent_asserted_chunks = 0
    receipt_missing_chunks = 0
    dialogue_supported_chunks = 0
    user_asserted_chunks = 0
    malformed_output = False
    disclaimer = None
    annotated_answer = ""

    def to_log_payload(self) -> dict:
        return {}


def test_the_trace_pair_is_not_applicable_rather_than_zero() -> None:
    """ГЛАВНОЕ: воспроизведение трассы — не мерили, а не провалили."""
    vector = compute_vector(
        report=_Report(), disagreements=[],
        question=_TRACE_QUESTION, answer=_TRACE_ANSWER,
    )
    assert not vector.relevance_applicable, (
        "бестемный вопрос снова измерен покрытием слов и выдан за суждение"
    )
    assert vector.relevance_score is None


@pytest.mark.parametrize(
    "question",
    [
        # Непойманные при починке формы того же класса: другие глаголы, другие
        # прилагательные, другой суффикс местоимения, другой язык. Ни под одну
        # не заводилось правило — останавливать обязана структура слота.
        "посоветуй что-нибудь интересное",
        "расскажи чего-нибудь смешного",
        "спой какую-нибудь весёлую",
        "tell me something clever",
        "give me anything useful",
    ],
    ids=["advise", "genitive", "adjective-chain", "english", "anything"],
)
def test_unseen_forms_of_the_class_are_stopped_without_a_rule(
    question: str,
) -> None:
    """Критерий обучения: unseen-форма класса остановлена без готового правила."""
    vector = compute_vector(
        report=_Report(), disagreements=[], question=question,
        answer=_TRACE_ANSWER,
    )
    assert not vector.relevance_applicable, question
    assert vector.relevance_score is None


def test_an_indefinite_with_a_real_topic_is_still_measured() -> None:
    """Защита от вырождения: «что-нибудь про докер» — тема есть, замер есть.

    Заодно закрепляет токенизатор: при старом разрыве «что-нибудь» по дефису
    обломок «нибудь» вошёл бы в знаменатель и покрытие упало бы втрое.
    """
    vector = compute_vector(
        report=_Report(), disagreements=[],
        question="расскажи что-нибудь про докер",
        answer="Докер изолирует процессы контейнерами; образы собираются слоями.",
    )
    assert vector.relevance_applicable
    assert vector.relevance_score is not None and vector.relevance_score > 0.9


def test_a_real_off_topic_answer_is_still_accused() -> None:
    """Ломка наоборот: настоящий «ответ не про то» обязан остаться пойманным."""
    vector = compute_vector(
        report=_Report(), disagreements=[],
        question="как настроить докер на сервере",
        answer="Погода в Москве завтра переменная облачность, ветер северный.",
    )
    assert vector.relevance_applicable
    assert vector.relevance_score is not None and vector.relevance_score < 0.2


def test_descriptor_words_do_not_depress_a_measured_score() -> None:
    """«что-нибудь интересное про докер»: описание ответа — не тема вопроса."""
    score = relevance_score(
        "расскажи что-нибудь интересное про докер",
        "Докер изолирует процессы контейнерами; образы собираются слоями.",
    )
    assert score > 0.9


def test_a_topicless_axis_does_not_drag_the_overall_score() -> None:
    """Ноль вместо «не измерено» топил бы общий балл выдуманным наблюдением."""
    vector = compute_vector(
        report=_Report(), disagreements=[],
        question=_TRACE_QUESTION, answer=_TRACE_ANSWER,
    )
    assert vector.overall_confidence > 0.5, (
        f"неизмеренная ось всё ещё тянет общий балл вниз: "
        f"{vector.overall_confidence}"
    )
