"""MVP-14.3c — Knowledge Pipeline Integration tests."""

from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.knowledge_pipeline import (
    ClaimExtractor,
    ConflictResolver,
    KnowledgeWritePolicy,
)
from core.source_registry import SourceRegistry


def _file_ev(path: str, text: str):
    return make_evidence(
        kind="file",
        source_id=f"file:{path}",
        obtained_via="file_read",
        claim=f"Contents of workspace file {path}",
        excerpt=text,
        confidence=0.90,
    )


def test_claim_extractor_extracts_sentence_claims_not_generic_source_claims():
    evidence = _file_ev(
        "notes.txt",
        "Agent memory stores verified claims. API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123.",
    )
    source = SourceRegistry.from_provenance(ProvenanceChain()).register_source(
        type="file",
        title="notes.txt",
        locator="notes.txt",
        trust_level=0.90,
    )

    claims = ClaimExtractor().extract(evidence, source=source, rank=None)

    assert [c.text for c in claims] == ["Agent memory stores verified claims."]


def test_conflict_resolver_does_not_promote_weak_unverified_claim():
    # Two WEAK (unverified) sources agreeing must NOT manufacture a "verified"
    # fact — weak corroboration stays weak.
    registry = SourceRegistry()
    a = registry.register_source(type="file", title="a", locator="a.txt", trust_level=0.9)
    b = registry.register_source(type="file", title="b", locator="b.txt", trust_level=0.9)
    registry.register_claim(
        source_id=a.id, text="Agent mode is local.", confidence=0.9, status="unverified"
    )
    registry.register_claim(
        source_id=b.id, text="Agent mode is local.", confidence=0.9, status="unverified"
    )

    resolved, report = ConflictResolver().resolve(registry)

    assert report.count == 0
    assert {c.status for c in resolved.claims} == {"unverified"}


def test_knowledge_write_policy_rejects_promotional_hype_claim():
    # Truth/Hype antibody wiring: a loud, substance-free marketing claim from a
    # trusted source must still be REJECTED — hype is not knowledge.
    registry = SourceRegistry()
    source = registry.register_source(
        type="documentation", title="docs", locator="docs", trust_level=0.90
    )
    hype = registry.register_claim(
        source_id=source.id,
        text="Our revolutionary, game-changing platform delivers seamless, "
             "world-class synergy!",
        confidence=0.95,
    )
    policy = KnowledgeWritePolicy()

    decision = policy.decide(hype, source=source)
    assert decision.decision == "reject"
    assert "hype" in decision.reasons[0]


# ── Non-asserting sources must not become long-term "knowledge" ──────────────
#
# MIR-054 already established (for conflict detection) that log, tool_output,
# test_result, memory and code_repository sources do not ASSERT anything:
# they are observations of one moment or the agent's own prior output. The
# write gate must consult the same doctrine — otherwise a diagnostic run can
# restate its own log lines as durable "facts", which is exactly what
# run_ab3f4bb672… did on 2026-07-31: five records like
# «strategy_classified: <timestamp> Source: log:log_event:…» were saved with
# Confidence 0.85. DEFAULT_SOURCE_TRUST rates log at 0.88 and test_result at
# 0.95, so trust alone can never stop them.

def _sentence_claim(registry: SourceRegistry, source, text: str):
    return registry.register_claim(
        source_id=source.id,
        text=text,
        confidence=0.85,
    )


def test_knowledge_write_policy_rejects_test_result_sourced_claim():
    registry = SourceRegistry()
    source = registry.register_source(
        type="test_result",
        title="pytest run",
        locator="pytest:tests/test_loop.py",
        trust_level=0.95,
    )
    claim = _sentence_claim(
        registry, source, "All twelve loop tests passed in under three seconds",
    )

    decision = KnowledgeWritePolicy().decide(claim, source=source)

    assert decision.decision == "reject"
    assert "does not assert" in decision.reasons[0]


def test_write_gate_and_conflict_resolver_share_the_non_asserting_doctrine():
    # Single source of truth: the gate must consult the same frozenset the
    # conflict resolver uses (MIR-054), so the two subsystems can never drift
    # apart about what counts as an assertion.
    from core.knowledge_pipeline import _NON_ASSERTING_SOURCE_TYPES

    policy = KnowledgeWritePolicy()
    registry = SourceRegistry()
    for source_type in sorted(_NON_ASSERTING_SOURCE_TYPES):
        source = registry.register_source(
            type=source_type,  # type: ignore[arg-type]
            title=f"{source_type} source",
            locator=f"{source_type}:probe",
            trust_level=0.99,
        )
        claim = _sentence_claim(
            registry, source, "This sentence looks exactly like a real fact",
        )
        decision = policy.decide(claim, source=source)
        assert decision.decision == "reject", source_type
