"""Group 3.4 — focused verifier trace for the `[memory:<id>]` laundering (MIR-042).

Question M0 deferred: does a matched `[memory:<id>]` citation actually count as
`verified`, and does the verifier distinguish the *trust* of the cited record
(user-approved vs agent-auto vs ingested-source vs replay vs working artifact)?

These tests exercise `core.verifier_core.verify` directly with a crafted
provenance chain. Target (lifecycle) semantics: a citation resolving to an
agent-auto / unverified memory record must NOT, by itself, count as independent
verification. Since the MIR-046 fix it does not; these tests pin that.

No production code is touched.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, evidence_from_memory_record
from core.verifier_core import verify


def _chain(*evs) -> ProvenanceChain:
    c = ProvenanceChain()
    for ev in evs:
        c.add(ev)
    return c


def _mem(record_id: str, content: str, source: str | None) -> object:
    return evidence_from_memory_record(
        record_id=record_id, content=content, source=source, created_at=None
    )


# ── fail-before: agent-auto memory citation counts as verified ───────────────

def test_agentauto_memory_citation_must_not_count_as_verified():
    """(MIR-042 fail-before) A claim citing an AGENT-AUTO persistent record is
    marked `verified` purely because the citation resolves to a record in the
    chain — not because the record's content was ever independently checked."""
    chain = _chain(_mem("rec1", "The capital of Australia is Sydney.", source="agent-auto"))
    report = verify(answer="The capital of Australia is Sydney [memory:rec1].", chain=chain)

    # TARGET: an agent-auto memory citation is not independent verification.
    assert report.verified_chunks == 0, (
        "agent-auto memory citation counted as verified "
        f"(verified_chunks={report.verified_chunks}) — memory presence is being "
        "treated as verification"
    )
