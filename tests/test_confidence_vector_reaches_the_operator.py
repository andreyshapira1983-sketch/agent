"""The three-axis confidence diagnosis must reach someone who can act on it.

MEASURED BY COMPLETE ENUMERATION, 2026-08-09. `compute_vector` is called in
exactly one place — `core/loop_verification.py` — and exactly two things were
done with the result: `log.log("confidence_vector", …)` and, on failure,
`_sensor_failed`. The local `_cv` was then discarded. Every other occurrence of
the name in production is a string literal naming the test file.

So the whole vector was journal-only, `overall_confidence` included. Its own
docstring promises "a near-zero score on any axis collapses the overall — a
weakest-link semantics that matches operator intuition", and that semantics
reached nothing.

WHAT IT COST, twice in one evening. Two production runs answered a question the
operator had not asked, scoring `relevance_score` 0.051 and 0.231. Both shipped.
Both passed verification — 27/28 and 14/16 — because verification measures
CITATION INTEGRITY, not whether the answer addresses the question. Those are
different quantities, and only one of them had a consumer.

`answer_enforcement` read `verified_ratio` and let both through. The tail the
operator sees said "уверенность: средняя", computed from the same verified
ratio. The number that knew was in the journal.

These tests pin the connection: the vector reaches the summary, and a low
relevance is stated in the operator-facing tail rather than left in a log.
"""
from __future__ import annotations

import pytest

from core.confidence_vector import compute_vector
from core.verification_summary import build_verification_summary
from core.verifier_models import ClaimChunk, VerificationReport


def _report(verified: int, total: int) -> VerificationReport:
    chunks = tuple(
        ClaimChunk(text=f"claim {i}", citations=(), matched_evidence_ids=(),
                   verdict="verified" if i < verified else "unverified")
        for i in range(total)
    )
    return VerificationReport(
        total_chunks=total, verified_chunks=verified,
        unverified_chunks=total - verified, cited_but_unmatched_chunks=0,
        self_declared_chunks=0, structural_chunks=0, chunks=chunks,
        annotated_answer="", fully_unverified=False, chain_was_empty=False,
    )


_ON_TOPIC_Q = "как устроен планировщик замены источников в цикле агента"
_ON_TOPIC_A = (
    "Планировщик замены источников в цикле агента устроен так: он выбирает "
    "источники и передаёт их дальше по циклу агента."
)
_OFF_TOPIC_A = (
    "Доктрина самовосстановления требует классификации фактов на четыре "
    "категории, а контроллер хранит уроки через отдельный метод."
)


def test_the_two_answers_really_differ_in_relevance() -> None:
    """PRECONDITION: without this the assertions below prove nothing."""
    on = compute_vector(report=_report(14, 16), disagreements=(),
                        question=_ON_TOPIC_Q, answer=_ON_TOPIC_A)
    off = compute_vector(report=_report(14, 16), disagreements=(),
                         question=_ON_TOPIC_Q, answer=_OFF_TOPIC_A)
    assert on.relevance_score > off.relevance_score, (
        f"on-topic {on.relevance_score:.3f} vs off-topic {off.relevance_score:.3f}"
    )


def test_a_low_relevance_answer_says_so_in_the_operator_tail() -> None:
    vector = compute_vector(report=_report(14, 16), disagreements=(),
                            question=_ON_TOPIC_Q, answer=_OFF_TOPIC_A)
    summary = build_verification_summary(_report(14, 16), vector=vector)

    assert "соответствие вопросу" in summary.tail.lower(), (
        "the operator-facing tail is silent about relevance; the run that "
        f"scored {vector.relevance_score:.3f} said only 'уверенность: средняя'"
    )


def test_an_on_topic_answer_is_not_warned_about() -> None:
    """GUARD: a warning on every answer would be noise, not a signal."""
    vector = compute_vector(report=_report(16, 16), disagreements=(),
                            question=_ON_TOPIC_Q, answer=_ON_TOPIC_A)
    summary = build_verification_summary(_report(16, 16), vector=vector)
    assert "может отвечать не на заданный вопрос" not in summary.tail


def test_the_summary_still_works_without_a_vector() -> None:
    """Back-compat: the vector is optional and its absence changes nothing."""
    summary = build_verification_summary(_report(14, 16))
    assert "подтверждено 14 из 16" in summary.tail
    assert "соответствие вопросу" not in summary.tail.lower()


def test_the_verified_ratio_wording_is_not_replaced_by_relevance() -> None:
    """Two different questions, two different numbers — neither may absorb the other.

    Citation integrity and task relevance answer different things. Collapsing
    them into one word would destroy exactly the information this repair exists
    to surface.
    """
    vector = compute_vector(report=_report(14, 16), disagreements=(),
                            question=_ON_TOPIC_Q, answer=_OFF_TOPIC_A)
    summary = build_verification_summary(_report(14, 16), vector=vector)
    assert "подтверждено 14 из 16" in summary.tail
    assert "уверенность:" in summary.tail


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")
