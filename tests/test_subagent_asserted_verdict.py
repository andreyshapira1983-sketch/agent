"""Trust-architecture tests: sub-agent answers are witnesses, not sources.

A claim verified only by a sub-agent's own answer must NOT count as
`verified` unless the sub-agent itself produced external evidence
(file / web_page / etc.). Otherwise the verifier is just trusting one
LLM that trusted another LLM.
"""
from __future__ import annotations

from core.evidence import Evidence, ProvenanceChain, make_evidence
from core.verifier import (
    _is_derivative_subagent_evidence,
    verify,
)
from core.low_evidence_policy import evaluate_low_evidence_policy


def _subagent_evidence(
    *, contract: str, external: int, kinds: str = "",
    extra_excerpt: str = "",
) -> Evidence:
    """Build an Evidence record that mimics what the parent loop caches
    after a `spawn_subagent` tool call (memory artefact)."""
    excerpt = (
        f"[subagent-meta external_evidence_count={external}"
        f" external_kinds={kinds}]\n"
        f"SubAgent name={contract!r}\n"
        f"  role: tester\n"
        f"  answer: {extra_excerpt}"
    )
    return make_evidence(
        kind="memory",
        source_id=f"memory:working_turn_1_subagent_{contract}",
        obtained_via="working_memory",
        claim=f"Sub-agent {contract} report",
        excerpt=excerpt,
    )


class TestDerivativeDetection:
    def test_marker_zero_external_is_derivative(self):
        ev = _subagent_evidence(contract="A", external=0)
        assert _is_derivative_subagent_evidence(ev) is True

    def test_marker_with_external_is_not_derivative(self):
        ev = _subagent_evidence(
            contract="A", external=2, kinds="web_page,file",
        )
        assert _is_derivative_subagent_evidence(ev) is False

    def test_unrelated_evidence_is_not_derivative(self):
        ev = make_evidence(
            kind="web_page",
            source_id="web:https://example.com",
            obtained_via="web_fetch",
            claim="page",
            excerpt="some external content",
        )
        assert _is_derivative_subagent_evidence(ev) is False

    def test_legacy_subagent_artefact_without_marker_is_derivative(self):
        # Older cached artefact (pre-marker) — the source_id alone is
        # enough to infer it was a sub-agent product, and the excerpt
        # contains no external citation grammar -> derivative.
        ev = make_evidence(
            kind="memory",
            source_id="memory:working_turn_1_subagent_Old",
            obtained_via="working_memory",
            claim="legacy",
            excerpt="some hand-written summary text",
        )
        assert _is_derivative_subagent_evidence(ev) is True

    def test_legacy_subagent_artefact_with_external_citation_is_not(self):
        ev = make_evidence(
            kind="memory",
            source_id="memory:working_turn_1_subagent_Old",
            obtained_via="working_memory",
            claim="legacy",
            excerpt="see [web:https://example.com] for details",
        )
        assert _is_derivative_subagent_evidence(ev) is False


class TestSubagentAssertedVerdict:
    def test_three_subagents_zero_external_demoted(self):
        # 3 subagents, 0 external evidence each — every chunk that
        # "matches" their memory artefacts must be `subagent_asserted`,
        # NOT `verified`.
        chain = ProvenanceChain()
        for name in ("LaunchCases", "Validation", "Channels"):
            chain.add(_subagent_evidence(contract=name, external=0))

        answer = (
            "Conclusion: launching in 30 days is feasible "
            "[memory:memory:working_turn_1_subagent_LaunchCases].\n"
            "Facts:\n"
            "Beta-testing with 20-100 users is recommended "
            "[memory:memory:working_turn_1_subagent_Validation].\n"
            "Product Hunt converts at 2-5 percent for dev tools "
            "[memory:memory:working_turn_1_subagent_Channels].\n"
            "Sources: 3 sub-agents.\n"
            "Confidence: high\n"
            "Unverified: nothing\n"
            "Safety: ok"
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 0, (
            f"Expected 0 verified, got {report.verified_chunks}; "
            f"verdicts={[c.verdict for c in report.chunks]}"
        )
        assert report.subagent_asserted_chunks >= 3
        # Confirm those 3 chunks are tagged with the new verdict.
        sub_verdicts = [
            c for c in report.chunks if c.verdict == "subagent_asserted"
        ]
        assert len(sub_verdicts) >= 3

    def test_subagent_with_external_evidence_stays_verified(self):
        # Sub-agent that DID fetch external evidence — its citations
        # remain admissible as `verified`.
        chain = ProvenanceChain()
        chain.add(_subagent_evidence(
            contract="WebResearcher", external=2,
            kinds="web_page,file",
        ))

        answer = (
            "Conclusion: result holds "
            "[memory:memory:working_turn_1_subagent_WebResearcher].\n"
            "Sources: 1 sub-agent.\n"
            "Confidence: high\n"
            "Unverified: nothing\n"
            "Safety: ok"
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks >= 1
        assert report.subagent_asserted_chunks == 0

    def test_low_evidence_policy_triggers_on_subagent_only(self):
        # End-to-end: 3 sub-agents, 0 external each, 11 claim chunks
        # all matching them. Verifier puts 0 in `verified_chunks` and
        # 11 in `subagent_asserted_chunks`. Low-evidence policy must
        # treat this as severely under-supported and truncate.
        chain = ProvenanceChain()
        for name in ("A", "B", "C"):
            chain.add(_subagent_evidence(contract=name, external=0))

        # Build an answer with 8 claim chunks each citing a sub-agent.
        chunks = [
            f"Claim {i} stands "
            f"[memory:memory:working_turn_1_subagent_A]."
            for i in range(8)
        ]
        answer = (
            "Conclusion: long polished plan with many steps.\n"
            "Facts:\n" + "\n".join(chunks) + "\n"
            "Sources: 3 sub-agents.\n"
            "Confidence: high\n"
            "Unverified: nothing\n"
            "Safety: ok"
        )
        report = verify(answer=answer, chain=chain)

        # Sanity: trust-architecture demoted everything.
        assert report.verified_chunks == 0
        assert report.subagent_asserted_chunks >= 6

        # Low-evidence policy must fire because the unverified mass
        # (driven by subagent_asserted_chunks) crosses the floor.
        result = evaluate_low_evidence_policy(
            answer=answer,
            report=report,
            question="Plan a 30-day product launch.",
        )
        assert result.triggered is True
        assert result.unverified_total >= 6

    def test_log_payload_carries_subagent_asserted_count(self):
        chain = ProvenanceChain()
        chain.add(_subagent_evidence(contract="X", external=0))
        answer = (
            "Conclusion: a "
            "[memory:memory:working_turn_1_subagent_X].\n"
            "Sources: 1\nConfidence: high\nUnverified: nothing\nSafety: ok"
        )
        report = verify(answer=answer, chain=chain)
        payload = report.to_log_payload()
        assert "subagent_asserted_chunks" in payload
        assert payload["subagent_asserted_chunks"] == report.subagent_asserted_chunks


class TestSubagentRunResultMarker:
    def test_to_evidence_text_prepends_marker(self):
        from core.subagent_runner import SubAgentRunResult

        r = SubAgentRunResult(
            contract_name="TestAgent",
            role="tester",
            objective="test",
            answer="Conclusion: ok",
            trace_id="trace_123",
            status="success",
            external_evidence_count=0,
            external_evidence_kinds=(),
        )
        text = r.to_evidence_text()
        assert text.startswith(
            "[subagent-meta external_evidence_count=0 external_kinds=]"
        )

    def test_to_evidence_text_marker_lists_kinds(self):
        from core.subagent_runner import SubAgentRunResult

        r = SubAgentRunResult(
            contract_name="TestAgent",
            role="tester",
            objective="test",
            answer="Conclusion: ok",
            trace_id="trace_123",
            status="success",
            external_evidence_count=2,
            external_evidence_kinds=("web_page", "file"),
        )
        text = r.to_evidence_text()
        assert (
            "[subagent-meta external_evidence_count=2"
            " external_kinds=web_page,file]"
        ) in text

    def test_default_external_fields_are_zero(self):
        from core.subagent_runner import SubAgentRunResult

        r = SubAgentRunResult(
            contract_name="X",
            role="r",
            objective="o",
            answer="a",
            trace_id="t",
            status="success",
        )
        assert r.external_evidence_count == 0
        assert r.external_evidence_kinds == ()
