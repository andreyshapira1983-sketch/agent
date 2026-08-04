"""Tests for core.evidence_support — the support ratio AND its applicability.

The module this replaces (`core/confidence_gate.py`) computed one scalar it
called *confidence*. Measurement showed the scalar collapsed three different
situations into almost the same zero
(`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`):

    A  evidence was never required  ->  0.000
    B  evidence required, absent    ->  0.000
    C  citations resolve to nothing ->  0.000

On real traffic, 11 of 12 firings were case A, and on 6 of those the enforcing
layer had already recorded `no_evidence_expected` for the same turn.

So the ratio itself is kept and the *return type* changed: case A now reports
`applicable=False, score=None`, because a turn that owed no evidence has no
support ratio at all.
"""

from dataclasses import FrozenInstanceError, dataclass

import pytest

from core.evidence_support import (
    EvidenceSupportResult,
    compute_evidence_support,
    evaluate_evidence_support,
)


@dataclass
class _FakeReport:
    total_chunks: int = 0
    verified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    unverified_chunks: int = 0
    self_declared_chunks: int = 0
    dialogue_supported_chunks: int = 0
    chain_was_empty: bool = False
    fully_unverified: bool = False


# ── the ratio (unchanged arithmetic, plus dialogue support) ──────────────────

def test_none_report():
    assert compute_evidence_support(None) == 0.0


def test_zero_chunks():
    assert compute_evidence_support(_FakeReport(total_chunks=0)) == 0.0


def test_all_verified_returns_one():
    assert compute_evidence_support(_FakeReport(total_chunks=4, verified_chunks=4)) == 1.0


def test_penalises_fabricated_citations():
    # CORE-04: a cited_but_unmatched chunk is a citation that resolved to
    # NOTHING in the provenance chain — a fabricated attribution. It must be a
    # penalty (like unverified), never +0.5 half-credit.
    r = _FakeReport(total_chunks=4, cited_but_unmatched_chunks=4)
    assert compute_evidence_support(r) == 0.0
    mixed = _FakeReport(total_chunks=2, verified_chunks=1, cited_but_unmatched_chunks=1)
    assert compute_evidence_support(mixed) == pytest.approx(0.375)


def test_penalises_unverified():
    r = _FakeReport(total_chunks=4, verified_chunks=2, unverified_chunks=2)
    assert compute_evidence_support(r) == pytest.approx(0.375)


def test_clamped_to_zero():
    assert compute_evidence_support(_FakeReport(total_chunks=2, unverified_chunks=2)) == 0.0


def test_dialogue_support_counts(monkeypatch):
    """Issue #119: a claim about this session's own exchange IS supported.

    The truncation gate already counts it; an observer that did not would call
    a self-analysis turn "evidence required and absent".
    """
    r = _FakeReport(total_chunks=4, dialogue_supported_chunks=4)
    assert compute_evidence_support(r) == 1.0


# ── the three situations the old scalar could not tell apart ────────────────

def test_a_no_evidence_expected_has_no_score():
    """A — the question does not apply. Not a low score: NO score."""
    r = _FakeReport(total_chunks=6, self_declared_chunks=6, chain_was_empty=True)

    res = evaluate_evidence_support(r, evidence_expected=False)

    assert res.applicable is False
    assert res.score is None
    assert res.reason == "no_evidence_expected"
    assert res.below_threshold is False


def test_b_evidence_expected_and_absent_scores_zero():
    """B — a real deficiency, and it keeps its zero."""
    r = _FakeReport(total_chunks=6, unverified_chunks=6, fully_unverified=True)

    res = evaluate_evidence_support(r, evidence_expected=True)

    assert res.applicable is True
    assert res.score == 0.0
    assert res.reason == "measured"
    assert res.below_threshold is True
    assert res.citation_integrity_violation is False


def test_c_fabricated_citations_are_flagged_separately():
    """C — an integrity failure, not merely a low number."""
    r = _FakeReport(total_chunks=6, cited_but_unmatched_chunks=6, fully_unverified=True)

    res = evaluate_evidence_support(r, evidence_expected=True)

    assert res.applicable is True
    assert res.score == 0.0
    assert res.citation_integrity_violation is True


def test_integrity_violation_is_orthogonal_to_a_good_score():
    """A well-supported answer with one fabricated citation is still flagged."""
    r = _FakeReport(total_chunks=10, verified_chunks=9, cited_but_unmatched_chunks=1)

    res = evaluate_evidence_support(r, evidence_expected=True)

    assert res.score > 0.8
    assert res.below_threshold is False
    assert res.citation_integrity_violation is True


def test_a_and_b_are_distinguishable_at_the_same_score():
    """The defect in one assertion: same claims, different applicability."""
    r = _FakeReport(total_chunks=6, self_declared_chunks=6, chain_was_empty=True)

    not_owed = evaluate_evidence_support(r, evidence_expected=False)
    owed = evaluate_evidence_support(r, evidence_expected=True)

    assert not_owed.score is None and owed.score == 0.0
    assert not_owed.applicable is not owed.applicable


# ── edges ────────────────────────────────────────────────────────────────────

def test_no_claims_is_not_applicable():
    res = evaluate_evidence_support(_FakeReport(total_chunks=0), evidence_expected=True)
    assert res.applicable is False
    assert res.reason == "no_claims"


def test_none_report_is_not_applicable():
    res = evaluate_evidence_support(None, evidence_expected=True)
    assert res.applicable is False
    assert res.score is None
    assert res.reason == "no_report"


def test_below_threshold_needs_enough_claims():
    r = _FakeReport(total_chunks=1, unverified_chunks=1)
    res = evaluate_evidence_support(r, evidence_expected=True, min_total_chunks=3)
    assert res.score == 0.0
    assert res.below_threshold is False, "one claim is too coarse to call weak"


def test_invalid_parameters_raise():
    r = _FakeReport(total_chunks=2)
    with pytest.raises(ValueError):
        evaluate_evidence_support(r, evidence_expected=True, threshold=1.5)
    with pytest.raises(ValueError):
        evaluate_evidence_support(r, evidence_expected=True, min_total_chunks=-1)


def test_log_payload_shape():
    r = _FakeReport(total_chunks=4, unverified_chunks=4)
    payload = evaluate_evidence_support(r, evidence_expected=True).to_log_payload()
    for key in ("applicable", "score", "reason", "citation_integrity_violation",
                "total_chunks", "verified_chunks", "dialogue_supported_chunks",
                "fabricated_citations", "below_threshold", "threshold",
                "chain_was_empty", "fully_unverified"):
        assert key in payload


def test_not_applicable_payload_reports_a_null_score():
    r = _FakeReport(total_chunks=4, self_declared_chunks=4, chain_was_empty=True)
    payload = evaluate_evidence_support(r, evidence_expected=False).to_log_payload()
    assert payload["score"] is None
    assert payload["applicable"] is False


def test_result_is_frozen():
    res = evaluate_evidence_support(_FakeReport(total_chunks=2), evidence_expected=True)
    assert isinstance(res, EvidenceSupportResult)
    # `FrozenInstanceError`, а не голое `Exception`: проверяется КОНКРЕТНЫЙ
    # механизм. Широкий перехват зеленел бы и в том случае, если класс
    # перестал быть замороженным датаклассом и запрещает запись как-то иначе.
    with pytest.raises(FrozenInstanceError):
        res.score = 1.0  # type: ignore[misc]
