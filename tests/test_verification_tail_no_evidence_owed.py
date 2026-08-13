"""«Нулевая уверенность» — приговор должнику улик, а не тому, кто их не должен.

ЗАМЕР 2026-08-13, трасса trace_da0f132bd8e203a77ae5870fdbb39a90. Светская
реплика («скажи что-нибудь умное») прошла весь конвейер: `evidence_support`
вынес вердикт `applicable=False, reason=no_evidence_expected` — этот ход улик
не был должен. А хвост сводки в том же ходе сказал оператору «уверенность:
нулевая»: `_confidence_wording(0, 5)` вердикта не читал. Два узла одного хода
разошлись во мнении о том, были ли улики нужны, — ровно та развилка, ради
которой `core/evidence_support.py` переписывали (его же docstring предупреждал:
«callers that need that distinction must read evaluate_evidence_support»).

Эти тесты закрепляют: вердикт применимости доходит до композитора сводки, и
хвост «улики не требовались» отличим от хвоста «улики требовались и их нет».
"""
from __future__ import annotations

from core.confidence_vector import ConfidenceVector
from core.evidence_support import EvidenceSupportResult
from core.verification_summary import build_verification_summary
from core.verifier_models import ClaimChunk, VerificationReport


def _self_declared_report(total: int = 5) -> VerificationReport:
    chunks = tuple(
        ClaimChunk(text=f"claim {i}", citations=(), matched_evidence_ids=(),
                   verdict="self_declared")
        for i in range(total)
    )
    return VerificationReport(
        total_chunks=total, verified_chunks=0, unverified_chunks=0,
        cited_but_unmatched_chunks=0, self_declared_chunks=total,
        structural_chunks=0, chunks=chunks, annotated_answer="",
        fully_unverified=False, chain_was_empty=True,
    )


def _not_owed(**overrides) -> EvidenceSupportResult:
    base = {
        "applicable": False, "score": None, "reason": "no_evidence_expected",
        "total_chunks": 5, "chain_was_empty": True,
    }
    base.update(overrides)
    return EvidenceSupportResult(**base)


def test_a_turn_that_owed_no_evidence_is_not_called_zero_confidence() -> None:
    """ГЛАВНОЕ: воспроизведение трассы — хвост говорит «не требовалось»."""
    summary = build_verification_summary(
        _self_declared_report(), evidence_support=_not_owed(),
    )
    assert "нулевая" not in summary.tail, summary.tail
    assert "не требовалось" in summary.tail


def test_point_five_of_the_journal_says_not_applicable() -> None:
    summary = build_verification_summary(
        _self_declared_report(), evidence_support=_not_owed(),
    )
    assert "не применимо" in summary.confidence
    assert "нулевая" not in summary.confidence


def test_without_a_verdict_the_old_wording_stands() -> None:
    """Совместимость: сводка старше вердикта и обязана строиться без него."""
    summary = build_verification_summary(_self_declared_report())
    assert "уверенность: нулевая" in summary.tail


def test_a_turn_that_owed_evidence_and_brought_none_is_still_zero() -> None:
    """Ломка наоборот: настоящий должник улик приговор сохраняет."""
    owed = EvidenceSupportResult(
        applicable=True, score=0.0, reason="measured", total_chunks=5,
    )
    summary = build_verification_summary(
        _self_declared_report(), evidence_support=owed,
    )
    assert "уверенность: нулевая" in summary.tail


def test_fabricated_citations_forfeit_the_softer_wording() -> None:
    """Фабрикация цитат — ложность, которую «улики не требовались» не смывает."""
    summary = build_verification_summary(
        _self_declared_report(),
        evidence_support=_not_owed(citation_integrity_violation=True,
                                   fabricated_citations=2),
    )
    assert "не требовалось" not in summary.tail
    assert "уверенность: нулевая" in summary.tail


def test_the_relevance_warning_survives_the_softer_tail() -> None:
    """Оси ортогональны: «улик не должен» не гасит «ответ не про то»."""
    summary = build_verification_summary(
        _self_declared_report(),
        vector=ConfidenceVector(
            evidence_score=0.0, coherence_score=1.0,
            relevance_score=0.11, relevance_applicable=True,
            overall_confidence=0.1,
        ),
        evidence_support=_not_owed(),
    )
    assert "не требовалось" in summary.tail
    assert "не на заданный вопрос" in summary.tail


def test_the_loop_actually_hands_the_verdict_to_the_composer() -> None:
    """Проводка: параметр существует, потому что его кто-то передаёт.

    Урок 2026-08-09 (`test_confidence_vector_reaches_the_operator`): узел,
    чей результат только журналируется, для оператора не существует. Тот же
    режим отказа здесь ловится проверкой места вызова.
    """
    import inspect

    import core.loop_response_deciders
    src = inspect.getsource(core.loop_response_deciders)
    assert "evidence_support=" in src, (
        "build_verification_summary вызывается без вердикта применимости — "
        "хвост снова начнёт объявлять нулевую уверенность там, где улики "
        "не требовались"
    )
