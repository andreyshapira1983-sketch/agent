"""Память находит урок по смыслу, а не только по общим словам.

Замер 2026-09-23 после BM25: вопрос «поставь библиотеку» не находил урок
«ставя себе пакет, проверь окружение» — общих слов нет. Поиск по смыслу
(multilingual-e5-large-instruct) и выпуклая сумма со словами (Bruch et al.,
ACM TOIS 2023) на трёх наборах — 9 прежних вопросов, 6 отложенных, сказанных
другими словами, и 6 с точными именами — дали 13 попаданий из 21 против 10 у
одних слов. «Только смысл» набрал 14, но потерял чистый идентификатор
(`enumeration_count_reason`) — ровно тот провал, о котором пишет литература;
поэтому α = 0.5.

Здесь модель подменена детерминированным кодировщиком: проверяется проводка,
а не качество модели. Качество — замером на живой памяти (см. коммит).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

import core.memory_embeddings as me
from core.memory_policy import MemoryRetrievalPolicy
from core.models import MemoryRecord

_CONCEPTS = {
    "install": ("пакет", "библиотек", "постав", "став"),
    "book": ("книг", "глав", "оглавл"),
}


def _fake(texts: list[str]) -> np.ndarray:
    rows = []
    for t in texts:
        low = t.lower()
        v = np.array([float(any(m in low for m in marks)) for marks in _CONCEPTS.values()] + [0.1])
        rows.append(v / np.linalg.norm(v))
    return np.stack(rows)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(me.MODEL_ENV, raising=False)
    me.set_encoder(None)
    yield
    me.set_encoder(None)
    me._instruct = False


def _record(content: str, hours_ago: float = 0.0) -> MemoryRecord:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return MemoryRecord(content=content, type="semantic", tags=["fact"], owner="self",
                        source="agent-auto", created_at=stamp.isoformat())


PACKAGE = _record("Ставя себе пакет, проверь окружение.", hours_ago=24)
BOOK = _record("Недостающую главу книги нашёл в оглавлении.", hours_ago=1)
QUESTION = "поставь недостающую библиотеку"


def _policy(n: int = 2) -> MemoryRetrievalPolicy:
    policy = MemoryRetrievalPolicy(max_records=n)
    policy.lessons_by_kind = 0
    return policy


def test_a_paraphrase_without_shared_words_reaches_the_selection() -> None:
    """Без смысла урок про пакет не проходит даже допуск: общих слов нет."""
    assert PACKAGE not in _policy().select([PACKAGE, BOOK], QUESTION)

    me.set_encoder(_fake)
    assert PACKAGE in _policy().select([PACKAGE, BOOK], QUESTION)


def test_meaning_breaks_a_tie_that_words_cannot() -> None:
    """Одинаково по словам — выше та запись, что ближе по смыслу."""
    near = _record("Недостающую библиотеку ставь в своё окружение пакетом.", hours_ago=24)
    far = _record("Недостающую главу книги библиотеки нашёл в оглавлении.", hours_ago=1)
    me.set_encoder(_fake)
    assert _policy(1).select([near, far], QUESTION) == [near]


def test_without_a_model_the_selection_is_exactly_as_before() -> None:
    before = _policy().select([PACKAGE, BOOK], QUESTION)
    me.set_encoder(None)
    assert _policy().select([PACKAGE, BOOK], QUESTION) == before == [BOOK]


def test_a_failing_model_falls_back_to_words() -> None:
    def broken(_texts):
        raise RuntimeError("модель упала")

    me.set_encoder(broken)
    assert me.fused_relevance("вопрос", ["текст"], [1.0]) == ([1.0], None)
    assert _policy().select([PACKAGE, BOOK], QUESTION) == [BOOK]


def test_a_second_question_does_not_re_encode_the_memory() -> None:
    calls: list[int] = []

    def counting(texts):
        calls.append(len(texts))
        return _fake(texts)

    me.set_encoder(counting)
    me.semantic_scores("первый", ["запись один", "запись два"])
    me.semantic_scores("второй", ["запись один", "запись два"])
    assert calls == [2, 1, 1], "память перекодировалась на втором вопросе"


def test_the_instruct_model_gets_its_task_and_bare_passages() -> None:
    """Правило карточки multilingual-e5-large-instruct."""
    me._instruct = True
    assert me._query("вопрос") == f"Instruct: {me.INSTRUCT_TASK}\nQuery: вопрос"
    assert me._passage("запись") == "запись"
    me._instruct = False
    assert me._query("вопрос") == "query: вопрос"
    assert me._passage("запись") == "passage: запись"


def test_fusion_formulas() -> None:
    assert me.convex([0.0, 2.0], [1.0, 0.0], alpha=0.5) == [0.5, 0.5]
    assert me.convex([0.0, 2.0], [1.0, 0.0], alpha=1.0) == [1.0, 0.0]
    # RRF: 1/(60 + место), место с единицы.
    assert me.rrf([2.0, 1.0]) == pytest.approx([1 / 61, 1 / 62])
