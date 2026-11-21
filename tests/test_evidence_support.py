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

from dataclasses import dataclass

import pytest

from core.evidence_support import (
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


# ── edges ────────────────────────────────────────────────────────────────────


def test_none_report_is_not_applicable():
    res = evaluate_evidence_support(None, evidence_expected=True)
    assert res.applicable is False
    assert res.score is None
    assert res.reason == "no_report"


def test_invalid_parameters_raise():
    r = _FakeReport(total_chunks=2)
    with pytest.raises(ValueError):
        evaluate_evidence_support(r, evidence_expected=True, threshold=1.5)
    with pytest.raises(ValueError):
        evaluate_evidence_support(r, evidence_expected=True, min_total_chunks=-1)
