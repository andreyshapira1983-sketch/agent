"""P1/P2: confidence vector decomposition.

The single scalar from :mod:`core.evidence_support` hides whether the
agent is uncertain because of weak evidence, internal incoherence, or
topic drift. The vector exposes those three axes separately and
combines them with a weakest-link geometric mean.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.confidence_vector import (
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
    def test_empty_report_returns_zero(self):
        assert evidence_score(_Report(total_chunks=0)) == 0.0
        assert evidence_score(None) == 0.0


class TestRelevanceScore:
    def test_empty_input_neutral(self):
        assert relevance_score(None, "any answer") == 0.5
        assert relevance_score("any question", "") == 0.5
        assert relevance_score("", "") == 0.5
