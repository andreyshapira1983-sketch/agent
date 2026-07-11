"""P1/P2: confidence vector decomposition.

The single scalar from :mod:`core.confidence_gate` hides whether the
agent is uncertain because of weak evidence, internal incoherence, or
topic drift. The vector exposes those three axes separately and
combines them with a weakest-link geometric mean.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.confidence_vector import (
    ConfidenceVector,
    coherence_score,
    compute_vector,
    evidence_score,
    relevance_score,
)


@dataclass
class _Report:
    total_chunks: int = 0
    verified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    unverified_chunks: int = 0


class TestEvidenceScore:
    def test_all_verified_returns_one(self):
        assert evidence_score(_Report(total_chunks=5, verified_chunks=5)) == 1.0

    def test_empty_report_returns_zero(self):
        assert evidence_score(_Report(total_chunks=0)) == 0.0
        assert evidence_score(None) == 0.0

    def test_partial_credit_for_cited_unmatched(self):
        # 2 verified + 2 cited_but_unmatched out of 4: (2*1 + 2*0.5)/4 = 0.75.
        score = evidence_score(_Report(
            total_chunks=4, verified_chunks=2, cited_but_unmatched_chunks=2,
        ))
        assert score == 0.75

    def test_unverified_penalty(self):
        # 4 verified + 4 unverified: (4 - 1)/8 = 0.375.
        score = evidence_score(_Report(
            total_chunks=8, verified_chunks=4, unverified_chunks=4,
        ))
        assert abs(score - 0.375) < 1e-6


class TestCoherenceScore:
    def test_no_disagreements_returns_one(self):
        assert coherence_score([]) == 1.0
        assert coherence_score(None) == 1.0

    def test_high_severity_drops_score(self):
        score = coherence_score([{"severity": "high"}])
        assert score == 0.4  # 1.0 - 0.6

    def test_multiple_events_accumulate(self):
        # one high (0.6) + one medium (0.3) = 0.9 penalty, score 0.1.
        score = coherence_score([
            {"severity": "high"},
            {"severity": "medium"},
        ])
        assert abs(score - 0.1) < 1e-6

    def test_score_clamped_at_zero(self):
        # three high-severity events would penalise by 1.8 — clamp to 0.
        score = coherence_score([
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "high"},
        ])
        assert score == 0.0


class TestRelevanceScore:
    def test_full_overlap_returns_high(self):
        # Question entirely covered by answer.
        score = relevance_score(
            "What is the capital of France?",
            "The capital of France is Paris, a major European city.",
        )
        # capital + france are both content tokens covered.
        assert score >= 0.5

    def test_zero_overlap_returns_zero(self):
        # Use single-language sets so no stopword bridges leak across.
        score = relevance_score(
            "Какая столица Франции?",
            "Apples bananas pears watermelon strawberry blueberry.",
        )
        assert score == 0.0

    def test_empty_input_neutral(self):
        assert relevance_score(None, "any answer") == 0.5
        assert relevance_score("any question", "") == 0.5
        assert relevance_score("", "") == 0.5

    def test_score_in_range(self):
        for q, a in [
            ("vacancy copywriter remote 6 hours ago",
             "The vacancy is for a copywriter, posted 6 hours ago."),
            ("What is 2 + 2?", "Four."),
        ]:
            score = relevance_score(q, a)
            assert 0.0 <= score <= 1.0

    def test_russian_inflected_forms_match(self):
        # Morphology: the answer uses different case endings than the
        # question ("репозитория" -> "репозитории", "проблемы" present).
        # Exact-token matching scored this ~0.25; fuzzy prefix matching
        # plus framing stopwords should now recognise it as on-topic.
        score = relevance_score(
            "какие проблемы есть у моего репозитория",
            "В репозитории найдены проблемы: падающий тест и нет документации.",
        )
        assert score >= 0.8

    def test_framing_verbs_do_not_depress_score(self):
        # Imperative request verbs ("проверь", "скажи") and possessives
        # ("мой") describe how the question was asked, not its topic, so
        # they must not count against coverage.
        score = relevance_score(
            "проверь мой репозиторий и скажи какие проблемы",
            "Проверка репозитория: найдены проблемы в тестах.",
        )
        assert score >= 0.8

    def test_english_plural_matches_singular(self):
        score = relevance_score(
            "which tests are failing",
            "One failing test was found in the suite.",
        )
        assert score >= 0.5

    def test_fuzzy_match_no_false_positive_on_short_shared_prefix(self):
        # Unrelated words that merely share a short prefix must not match:
        # "программа" vs "проблема" share only "про" (< min prefix), and
        # none of the answer words are on-topic.
        score = relevance_score(
            "столица франции",
            "программа проверяет структуру каталога.",
        )
        assert score == 0.0


class TestComputeVector:
    def test_all_strong_overall_high(self):
        v = compute_vector(
            report=_Report(total_chunks=4, verified_chunks=4),
            disagreements=[],
            question="capital of france paris",
            answer="The capital of France is Paris.",
        )
        assert isinstance(v, ConfidenceVector)
        assert v.evidence_score == 1.0
        assert v.coherence_score == 1.0
        assert v.relevance_score >= 0.5
        assert v.overall_confidence > 0.7

    def test_weak_evidence_drops_overall(self):
        v = compute_vector(
            report=_Report(total_chunks=4, verified_chunks=0, unverified_chunks=4),
            disagreements=[],
            question="capital of france",
            answer="The capital of France is Paris.",
        )
        # evidence=0 -> overall collapses near zero (epsilon-floored).
        assert v.evidence_score == 0.0
        assert v.overall_confidence < 0.2

    def test_incoherent_drops_overall(self):
        v = compute_vector(
            report=_Report(total_chunks=4, verified_chunks=4),
            disagreements=[
                {"severity": "high"},
                {"severity": "high"},
                {"severity": "high"},
            ],
            question="capital of france",
            answer="The capital of France is Paris.",
        )
        assert v.coherence_score == 0.0
        assert v.overall_confidence < 0.2

    def test_irrelevant_drops_overall(self):
        v = compute_vector(
            report=_Report(total_chunks=4, verified_chunks=4),
            disagreements=[],
            question="столица франции",
            answer="Apples bananas pears watermelon strawberry.",
        )
        assert v.relevance_score == 0.0
        # Even with verified evidence + coherent subsystems, zero
        # relevance pulls overall down (weakest-link).
        assert v.overall_confidence < 0.5

    def test_payload_is_jsonable(self):
        v = compute_vector(
            report=_Report(total_chunks=2, verified_chunks=2),
            disagreements=[],
            question="x",
            answer="x",
        )
        payload = v.to_log_payload()
        assert set(payload.keys()) == {
            "evidence_score", "coherence_score",
            "relevance_score", "overall_confidence",
        }
        for v_ in payload.values():
            assert isinstance(v_, float)
            assert 0.0 <= v_ <= 1.0
