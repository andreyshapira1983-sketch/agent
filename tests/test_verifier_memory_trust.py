"""Group 3.4 — focused verifier trace for the `[memory:<id>]` laundering (MIR-042).

Question M0 deferred: does a matched `[memory:<id>]` citation actually count as
`verified`, and does the verifier distinguish the *trust* of the cited record
(user-approved vs agent-auto vs ingested-source vs replay vs working artifact)?

These tests exercise `core.verifier_core.verify` directly with a crafted
provenance chain. Target (lifecycle) semantics: a citation resolving to an
agent-auto / unverified memory record must NOT, by itself, count as independent
verification. On CURRENT code it does (verdict `verified`, `verified_chunks += 1`
purely because the citation resolves to a record in the chain), so the fail-before
tests below FAIL — that is the point. The documentation tests PASS and pin the
current behaviour so the fix has a baseline.

No production code is touched.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, evidence_from_memory_record, make_evidence
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


def test_verified_comes_only_from_citation_resolution_not_content():
    """(proof) The SAME wrong content is `verified` when the record is in the
    chain and `cited_but_unmatched` when it is not — so the verdict tracks
    citation *resolution*, never content truth.

    Seeded `user-explicit` since the MIR-046 fix: an agent-auto record no
    longer reaches `verified` at all, which would mask the property this test
    exists to show. The point stands — resolution, not truth, drives the
    verdict — it just has to be demonstrated on a record the verifier still
    trusts.
    """
    wrong = "The capital of Australia is Sydney [memory:rec1]."

    in_chain = verify(answer=wrong, chain=_chain(_mem("rec1", "…", source="user-explicit")))
    not_in_chain = verify(answer=wrong, chain=ProvenanceChain())

    assert in_chain.verified_chunks == 1              # resolves → verified
    assert not_in_chain.verified_chunks == 0          # no record → not verified
    assert not_in_chain.cited_but_unmatched_chunks >= 1


# ── working-memory artifact, checked separately ──────────────────────────────

def test_working_memory_artifact_citation_also_counts_verified():
    """(proof, passes) A working-memory artifact (cached tool output) cited as
    `[memory:working_turn_...]` is ALSO marked verified — and via the identical
    path as an agent-auto persistent record (no distinction at the verdict)."""
    art = make_evidence(
        kind="memory",
        source_id="memory:working_turn_1_test_results",
        obtained_via="working_memory",
        claim="Cached tool output from turn 1",
        excerpt="exit_code=0",
    )
    report = verify(answer="Tests passed [memory:working_turn_1_test_results].", chain=_chain(art))
    assert report.verified_chunks == 1  # documents current behaviour


# ── fail-before: verifier does not distinguish trust class ───────────────────

def test_verifier_must_distinguish_userapproved_from_agentauto():
    """(fail-before) A user-approved record and an agent-auto record, both cited,
    receive the IDENTICAL `verified` verdict — the verifier does not consult the
    record's trust/provenance. Target: agent-auto must not auto-verify like a
    user-approved fact."""
    chain = _chain(
        _mem("u1", "User-approved fact.", source="user-explicit"),
        _mem("a1", "Agent-auto claim.", source="agent-auto"),
    )
    report = verify(
        answer="Fact one [memory:u1]. Fact two [memory:a1].",
        chain=chain,
    )
    verdicts = report.to_log_payload()["verdicts"]

    # CURRENT: both are "verified" (no trust distinction) → this assertion fails.
    assert verdicts.count("verified") <= 1, (
        f"verifier gave identical 'verified' to user-approved AND agent-auto "
        f"records (verdicts={verdicts}) — it does not distinguish trust class"
    )
