"""Совпадение слов не вправе объявлять «не по теме» то, что сошлось с уликами.

Background: docs/CODE_NOTES.md, "The measure punished being answered".
"""
from __future__ import annotations

from dataclasses import dataclass

from core.verification_summary import build_verification_summary


@dataclass
class _Chunk:
    verdict: str
    matched_evidence_ids: tuple = ()


@dataclass
class _Report:
    chunks: list
    verified_chunks: int
    dialogue_supported_chunks: int = 0
    user_asserted_chunks: int = 0
    chain_was_empty: bool = False


@dataclass
class _Vector:
    relevance_score: float
    relevance_applicable: bool = True


def _report(*, verified: int, unverified: int) -> _Report:
    chunks = [_Chunk("verified") for _ in range(verified)]
    chunks += [_Chunk("unverified") for _ in range(unverified)]
    return _Report(chunks=chunks, verified_chunks=verified)


def test_a_fully_verified_answer_is_not_told_it_missed_the_question():
    """Замер 2026-08-15: 14 из 45 полностью подтверждённых ответов получали
    «может отвечать не на заданный вопрос». Четыре прочитаны глазами — все по
    теме. Один дословно: вопрос «посмотри инструменты, конкретно…», ответ
    перечисляет содержимое `tools/`, 4 из 4 подтверждено, оценка 0.25.
    """
    summary = build_verification_summary(
        _report(verified=4, unverified=0), vector=_Vector(0.25),
    )

    assert "Соответствие вопросу" not in summary.tail


def test_a_partly_verified_answer_still_gets_the_warning():
    """Граница проведена здесь НАРОЧНО, и она стоит одного известного промаха.

    Живой ход 2026-08-15: «подтверждено 2 из 3», оценка 0.32, ответ верен — два
    файла названы правильно, и предупреждение на нём ложное. Но существующий
    тест (`test_a_low_relevance_answer_says_so_in_the_operator_tail`) держит
    случай посильнее: 14 из 16 подтверждено, а ответ говорит не о том. Цепочка
    улик за ход набирает материал не об одном предмете, и обосноваться в ней,
    отвечая на другое, можно. Полное подтверждение такой лазейки не оставляет,
    частичное — оставляет.
    """
    summary = build_verification_summary(
        _report(verified=2, unverified=1), vector=_Vector(0.32),
    )

    assert "Соответствие вопросу: 0.32" in summary.tail


def test_an_answer_with_nothing_verified_still_gets_the_warning():
    """Улов не отдан. Там, где прямой проверки нет, прокси — единственное, что
    есть, и на мутных ходах он ловит 44%.
    """
    summary = build_verification_summary(
        _report(verified=0, unverified=5), vector=_Vector(0.09),
    )

    assert "Соответствие вопросу: 0.09" in summary.tail


def test_a_high_score_says_nothing_either_way():
    """Предупреждение — это предупреждение, а не отчёт о числе."""
    summary = build_verification_summary(
        _report(verified=0, unverified=3), vector=_Vector(0.90),
    )

    assert "Соответствие вопросу" not in summary.tail


def test_an_inapplicable_score_is_still_silent():
    """Между разными системами письма покрытие слов не измеряет ничего —
    правило 2026-08-10 не тронуто.
    """
    summary = build_verification_summary(
        _report(verified=0, unverified=3),
        vector=_Vector(0.01, relevance_applicable=False),
    )

    assert "Соответствие вопросу" not in summary.tail
