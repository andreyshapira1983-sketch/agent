"""R8 и R9 (2026-08-13, живые трейсы bd02fff1 и ac75fc92): честность — не ложь.

R8: гейт литералов опровергал истинные утверждения против УСЕЧЁННОЙ улики —
`evidence_budget` вырезал часть с литералом (`kept 9617 of 12191`), и «нет в
вырезке» читалось как «нет в источнике». Семь ложных REFUTED за один ход.
Инвариант — пятый гейт, дословно: неполный поиск не доказывает отсутствия;
усечённая улика (маркер `[INTENT-BUDGET:`/`[TOTAL-BUDGET:`) не имеет права
опровергать по отсутствию. Доказывать ПРИСУТСТВИЕ она может по-прежнему.

R9: детектор противоречий матчил ПРЕДМЕТЫ, а не суждения: «файл есть в
листинге» (Facts) + «содержимое файла не читал» (Unverified) давало
self_contradicted×4, перепись тела и карантин. Лучший эпистемический ответ дня
получил худший балл — чем честнее граница знания, тем сильнее наказание.
Инвариант: заявление о границе знания («не читал», «нет данных», «не
передавали») не оспаривает утверждение о существовании; противоречие — это
снятие ТОГО ЖЕ суждения.
"""
from __future__ import annotations

from core.answer_contradiction import contradicted_claims
from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _chain_one(excerpt: str) -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="tests/output.txt",
                            obtained_via="t", claim="", excerpt=excerpt,
                            confidence=0.9))
    return chain


_ANSWER_NAMING_CUT_LITERAL = (
    "Conclusion: The gap lives in core/loop_attempt.py. [file:tests/output.txt]\n"
    "Facts:\n- The gap lives in core/loop_attempt.py [file:tests/output.txt]\n"
    "Sources:\n1. file:tests/output.txt - out\n"
    "Confidence: high\nUnverified: nothing\n"
)


def test_a_truncated_excerpt_cannot_prove_absence() -> None:
    """ГЛАВНОЕ R8: литерал за срезом — UNKNOWN, не опровержение."""
    excerpt = (
        "XFAIL notes begin here and the relevant module is"
        "\n...[INTENT-BUDGET: 49 of 12000 chars; head only]"
    )
    report = verify(answer=_ANSWER_NAMING_CUT_LITERAL, chain=_chain_one(excerpt),
                    user_question="where is the gap")
    assert report.refuted_chunks == 0, [
        (c.verdict, getattr(c.reason, "code", None)) for c in report.chunks
    ]


def test_a_complete_excerpt_still_refutes() -> None:
    """ПРЕДОХРАНИТЕЛЬ R8: полная улика опровергает как раньше."""
    report = verify(answer=_ANSWER_NAMING_CUT_LITERAL,
                    chain=_chain_one("The notes name only core/loop.py here.\n"),
                    user_question="where is the gap")
    assert report.refuted_chunks >= 1


def test_a_truncated_excerpt_still_proves_presence() -> None:
    """Усечение не мешает подтверждать: присутствие в вырезке — присутствие."""
    excerpt = (
        "The gap lives in core/loop_attempt.py, swallowed whole."
        "\n...[TOTAL-BUDGET: trimmed to 55 of 9000 chars to fit 30000-char total evidence budget]"
    )
    report = verify(answer=_ANSWER_NAMING_CUT_LITERAL, chain=_chain_one(excerpt),
                    user_question="where is the gap")
    assert report.refuted_chunks == 0
    assert report.verified_chunks >= 1


def test_a_knowledge_boundary_is_not_a_contradiction() -> None:
    """ГЛАВНОЕ R9: «не читал содержимое» не спорит с «есть в списке»."""
    answer = (
        "Conclusion: файл core/loop_attempt.py есть в листинге. [file:core/]\n"
        "Facts:\n- core/loop_attempt.py присутствует в листинге core/ [file:core/]\n"
        "Sources:\n1. file:core/ - listing\n"
        "Confidence: medium\n"
        "Unverified:\n"
        "- Содержимое core/loop_attempt.py — точная логика не читалась, "
        "данные не передавались.\n"
        "Safety: nothing\n"
    )
    assert contradicted_claims(answer) == ()


def test_a_real_self_contradiction_is_still_caught() -> None:
    """ПРЕДОХРАНИТЕЛЬ R9: живой класс 2026-08-10 не открывается обратно."""
    answer = (
        "Conclusion: планировщик использует core/loop_attempt.py. [file:core/]\n"
        "Facts:\n- core/loop_attempt.py обрабатывает попытки [file:core/]\n"
        "Sources:\n1. file:core/ - listing\n"
        "Confidence: high\n"
        "Unverified:\n- Утверждение про core/loop_attempt.py не подтвердилось "
        "и может быть неверным.\n"
    )
    subjects = [c.subject for c in contradicted_claims(answer)]
    assert "core/loop_attempt.py" in subjects
