"""Regression: an answer whose UNVERIFIED support outnumbers its verified support
must be `partial`, not banked as a clean `success` (CORE-01 / MGA-02).

The old rule demoted to `partial` only when ``verified == 0``, so a single lucky
verified chunk immunised an answer with many more unverified chunks: it banked as
`success` (quality ≈0.09), could graduate into a reusable procedure, and be fed
back as experience. The rule now mirrors the existing `weak >= verified` guard —
a relative-majority test, no magic threshold.

Scope: this does NOT touch the zero-evidence general-knowledge case (verified=0,
unverified=0 stays `success`, neutral) — that relevance/usefulness question is a
separate, calibration-heavy concern.
"""
from __future__ import annotations

from core.smart_memory import episode_from_agent_cycle, procedure_from_episode


def _ep(verified: int, unverified: int, weak: int = 0):
    return episode_from_agent_cycle(
        goal="g",
        question="q",
        answer="a",
        tools_used=["web_search"],
        source_labels=["web:x"],
        verified_chunks=verified,
        unverified_chunks=unverified,
        weak_chunks=weak,
    )


def test_majority_unverified_is_partial_not_success():
    ep = _ep(verified=1, unverified=10)
    assert ep.outcome == "partial", f"expected partial, got {ep.outcome!r}"
    # A non-clean episode must not graduate into a reusable procedure.
    assert procedure_from_episode(ep) is None


def test_verified_majority_still_success():
    assert _ep(verified=8, unverified=1).outcome == "success"


def test_zero_evidence_general_knowledge_unchanged():
    # Deliberately out of scope: a pure general-knowledge answer stays success.
    assert _ep(verified=0, unverified=0).outcome == "success"
