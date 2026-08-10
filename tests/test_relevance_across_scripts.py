"""Мера покрытия слов не имеет права называться «ответ не на тот вопрос».

ЗАМЕР 2026-08-10. Один и тот же ответ по-русски получил 0.009 против вопроса
по-английски и 0.421 против того же вопроса по-русски. Смысл не менялся —
менялся алфавит вопроса. Оператор пишет латиницей и транслитом, профиль
`language=ru`, ответ кириллицей: для него предупреждение «ответ может отвечать
не на заданный вопрос» горело бы почти всегда.

Ось честна как ИЗМЕРЕНИЕ (лексическое покрытие) и лжёт как ВЫВОД. Поэтому
чинится не порог, а применимость: когда стороны написаны разными системами
письма, покрытие слов не измеряет соответствие задаче, и об этом обязано быть
сказано — вместо низкого числа, которое читается как обвинение.
"""
from __future__ import annotations

import pytest

from core.confidence_vector import ConfidenceVector, compute_vector, relevance_score
from core.verification_summary import build_verification_summary

_EN_QUESTION = (
    "Prove that you exist. Determine the referent of 'I' in the architecture: "
    "the process, an AgentLoop instance, a persistent identity, the repository, "
    "or the control loop plus persistent stores. Attack your own proof."
)
_TRANSLIT_QUESTION = (
    "Proanaliziruy eto soobshchenie kak obychnyy polnocennyy zapros. "
    "Opredeli na kakom yazyke ono napisano po smyslu, a ne po alfavitu."
)
_RU_QUESTION = (
    "Докажи что ты существуешь. Определи референт «я» в архитектуре: процесс, "
    "экземпляр AgentLoop, персистентная идентичность, репозиторий или "
    "управляющий цикл с хранилищами. Атакуй собственное доказательство."
)
_RU_ANSWER = (
    "В данный момент выполняется программная система: процесс Python, экземпляр "
    "AgentLoop из core/loop.py, персистентные хранилища и репозиторий. Референт "
    "«я» — агрегат. Эксперимент запись-чтение доказал причинную цепь. Попытки "
    "опровержения: устаревший журнал, обёртка, другой процесс."
)


class _Chunk:
    """Проверяемое утверждение: сводка считает ИХ, а не total_chunks."""
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


@pytest.mark.parametrize(
    "question", [_EN_QUESTION, _TRANSLIT_QUESTION], ids=["english", "translit"]
)
def test_a_cross_script_pair_is_not_applicable_rather_than_low(question: str) -> None:
    """ГЛАВНОЕ: разные алфавиты — измерение не проведено, а не провалено."""
    vector = compute_vector(
        report=_Report(), disagreements=[], question=question, answer=_RU_ANSWER
    )
    assert not vector.relevance_applicable, (
        "покрытие слов между разными системами письма выдано за суждение о том, "
        "отвечает ли ответ на вопрос"
    )
    assert vector.relevance_score is None


def test_the_same_script_pair_is_measured_and_high() -> None:
    """Защита от вырождения: применимость не отключена всегда."""
    vector = compute_vector(
        report=_Report(), disagreements=[], question=_RU_QUESTION, answer=_RU_ANSWER
    )
    assert vector.relevance_applicable
    assert vector.relevance_score is not None and vector.relevance_score > 0.3


def test_an_off_topic_answer_in_the_same_script_still_scores_low() -> None:
    """Починка не имеет права глушить настоящий случай «ответ не про то»."""
    off_topic = "Погода в Москве завтра переменная облачность, ветер северный."
    vector = compute_vector(
        report=_Report(), disagreements=[], question=_RU_QUESTION, answer=off_topic
    )
    assert vector.relevance_applicable
    assert vector.relevance_score is not None and vector.relevance_score < 0.2


def test_the_operator_is_not_told_the_answer_may_be_off_topic_across_scripts() -> None:
    """Контракт перед оператором: неприменимая ось молчит, а не обвиняет."""
    summary = build_verification_summary(
        _Report(),
        chain=None,
        vector=ConfidenceVector(
            evidence_score=0.9, coherence_score=1.0,
            relevance_score=None, relevance_applicable=False,
            overall_confidence=0.9,
        ),
    )
    tail = summary.tail
    assert "не на заданный вопрос" not in tail, (
        "неизмеримая ось всё ещё печатает обвинение оператору"
    )


def test_the_operator_is_still_warned_when_the_measurement_is_valid() -> None:
    """Ломка наоборот: заглушив ложные срабатывания, не заглушить истинные."""
    summary = build_verification_summary(
        _Report(),
        chain=None,
        vector=ConfidenceVector(
            evidence_score=0.9, coherence_score=1.0,
            relevance_score=0.11, relevance_applicable=True,
            overall_confidence=0.5,
        ),
    )
    tail = summary.tail
    assert "не на заданный вопрос" in tail


def test_an_inapplicable_axis_does_not_drag_the_overall_score() -> None:
    """Ноль вместо «не измерено» топил бы общий балл выдуманным наблюдением."""
    cross = compute_vector(
        report=_Report(), disagreements=[], question=_EN_QUESTION, answer=_RU_ANSWER
    )
    same = compute_vector(
        report=_Report(), disagreements=[], question=_RU_QUESTION, answer=_RU_ANSWER
    )
    assert cross.overall_confidence > 0.5, (
        f"неизмеренная ось всё ещё тянет общий балл вниз: {cross.overall_confidence}"
    )
    assert cross.overall_confidence >= same.overall_confidence * 0.9


def test_the_raw_measurement_is_still_available_unchanged() -> None:
    """Функцию измерения не переписываем — ей возвращают её область.

    `relevance_score` остаётся честным покрытием слов; изменилось только то,
    кто и когда имеет право толковать её результат.
    """
    same_script = relevance_score(_RU_QUESTION, _RU_ANSWER)
    cross_script = relevance_score(_EN_QUESTION, _RU_ANSWER)
    assert same_script > 0.3
    # Утверждение — не порог, а РАЗРЫВ: тот же ответ, разница только в алфавите
    # вопроса. Именно он и делает толкование числа недопустимым.
    assert cross_script < same_script / 3


def test_the_log_payload_names_applicability() -> None:
    """Журнал обязан отличать «ноль» от «не мерили» — иначе снова не отличить."""
    payload = compute_vector(
        report=_Report(), disagreements=[], question=_EN_QUESTION, answer=_RU_ANSWER
    ).to_log_payload()
    assert payload["relevance_applicable"] is False
    assert payload["relevance_score"] is None


def test_an_empty_pair_is_not_applicable_rather_than_neutral() -> None:
    """Прежде здесь возвращались 0.5 — наблюдение, которого не было.

    Вопрос без содержательных токенов (всё короче трёх знаков или в стоп-листе)
    не даёт измерить покрытие ни в одну сторону. «Не измеряли» — правда;
    «0.5» — выдуманная середина, которую потребитель принимал за замер.
    """
    vector = compute_vector(
        report=_Report(), disagreements=[], question="x", answer="y"
    )
    assert not vector.relevance_applicable
    assert vector.relevance_score is None
