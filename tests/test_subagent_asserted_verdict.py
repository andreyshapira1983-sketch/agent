"""Trust-architecture tests: sub-agent answers are witnesses, not sources.

A claim verified only by a sub-agent's own answer must NOT count as
`verified` unless the sub-agent itself produced external evidence
(file / web_page / etc.). Otherwise the verifier is just trusting one
LLM that trusted another LLM.
"""
from __future__ import annotations

from core.evidence import Evidence, ProvenanceChain, make_evidence
from core.low_evidence_policy import evaluate_low_evidence_policy
from core.verifier import (
    verify,
)


def _subagent_evidence(
    *, contract: str, external: int, kinds: str = "",
    extra_excerpt: str = "", source_id: str | None = None,
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
        source_id=source_id or f"memory:working_turn_1_subagent_{contract}",
        obtained_via="working_memory",
        claim=f"Sub-agent {contract} report",
        excerpt=excerpt,
    )


class TestSubagentAssertedVerdict:
    def test_subagent_with_external_evidence_is_still_witness(self):
        # Corrected trust model: even when the sub-agent self-reports
        # external evidence, the parent holds only the child's prose, so
        # the claim is a witness statement (subagent_asserted), never
        # `verified`.
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

        assert report.verified_chunks == 0
        assert report.subagent_asserted_chunks >= 1


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
