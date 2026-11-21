"""Fifteen templates, a table naming six: the class, not the samples.

Background: docs/CODE_NOTES.md, "A framework label is not an assertion".
"""
from __future__ import annotations

import pytest

from core.knowledge_pipeline import ClaimExtractor, KnowledgeWritePolicy
from core.source_registry import SourceRecord, source_type_from_evidence

# The two the other gates never caught: neither the word table nor the
# source-type rule stops them. Measured 2026-08-15, both reached `save`.
_UNGUARDED = [
    ("web_page", "rss_fetch", "article", "Fetched RSS/Atom feed http://e/rss"),
    ("user_explicit", "user_input", "user", "User explicitly directed"),
]


@pytest.mark.parametrize("kind,via,stype,label", _UNGUARDED)
def test_the_labels_no_other_gate_caught(kind, via, stype, label):
    """The unseen form: same class, a template the repair was not fitted to."""
    from core.evidence import make_evidence

    evidence = make_evidence(
        kind=kind, source_id=f"{kind}:x", obtained_via=via, claim=label,
        excerpt="", confidence=0.9,
    )
    source = SourceRecord(
        id=evidence.source_id, type=source_type_from_evidence(evidence),
        locator="x", title="x", trust_level=0.95,
    )
    assert source.type == stype, "precondition: this source type does assert"

    policy = KnowledgeWritePolicy()
    for claim in ClaimExtractor().extract(evidence, source=source):
        if claim.text.strip() == label:
            assert policy.decide(claim, source=source).decision == "reject", (
                f"our own label reached durable memory: {label!r}"
            )
