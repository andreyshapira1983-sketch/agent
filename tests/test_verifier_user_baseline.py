"""P0: verifier materialises a user_explicit baseline from user_question.

Without this, claims that paraphrase or quote the operator's own input
resolve to `cited_but_unmatched [user]` because the chain has no record
of kind `user_explicit`. The baseline must live ONLY inside the
verifier — peripheral systems (source_registry, evidence_collected,
knowledge_pipeline) keep their existing contracts.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


class TestUserBaselineMaterialised:
    def test_user_citation_resolves_when_user_question_provided(self):
        chain = ProvenanceChain()
        report = verify(
            answer="Вакансия копирайтера опубликована 6 часов назад [user].",
            chain=chain,
            user_question=(
                "Опубликовано 6 часов назад. По всему миру. Полная занятость."
            ),
        )
        assert report.cited_but_unmatched_chunks == 0
        assert report.verified_chunks >= 1
        assert "[verified:user]" in report.annotated_answer

    def test_no_baseline_without_user_question_kwarg(self):
        # Default behaviour preserved: a [user] citation against an
        # empty chain is unmatched when no user_question is supplied.
        chain = ProvenanceChain()
        report = verify(
            answer="Some claim [user].",
            chain=chain,
        )
        assert report.verified_chunks == 0

    def test_baseline_does_not_mutate_caller_chain(self):
        # Caller's chain must not gain a synthetic user_explicit record.
        chain = ProvenanceChain()
        chain.add(make_evidence(
            kind="file", source_id="file:a.md",
            obtained_via="file_read",
            claim="A markdown file",
            excerpt="Some content.",
        ))
        before_kinds = [ev.kind for ev in chain.evidences]
        verify(
            answer="Claim [file:a.md].",
            chain=chain,
            user_question="anything",
        )
        after_kinds = [ev.kind for ev in chain.evidences]
        assert before_kinds == after_kinds
        assert "user_explicit" not in after_kinds

    def test_chain_was_empty_reflects_external_sources_only(self):
        # The "no-chain" disclaimer fires on absence of EXTERNAL sources;
        # the synthetic baseline must not flip chain_was_empty to False.
        chain = ProvenanceChain()
        report = verify(
            answer="Statement of fact [user].",
            chain=chain,
            user_question="A statement of fact.",
        )
        assert report.chain_was_empty is True

    def test_chain_was_not_empty_when_external_evidence_present(self):
        chain = ProvenanceChain()
        chain.add(make_evidence(
            kind="file", source_id="file:doc.md",
            obtained_via="file_read",
            claim="A workspace doc",
            excerpt="Hello.",
        ))
        report = verify(
            answer="The doc says hello [file:doc.md].",
            chain=chain,
            user_question="What does the doc say?",
        )
        assert report.chain_was_empty is False
