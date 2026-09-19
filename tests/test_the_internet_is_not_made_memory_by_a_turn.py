"""An ordinary turn does not make what it read on the internet a durable fact.

Two worlds (operator, 2026-09-19): the local library is the source of truth;
the internet is a source still to be qualified. Fourth web run, N06: the page
https://www.rfc-editor.org/info/rfc6749/ (type «documentation», trust 0.75)
wrote four «facts» into long-term memory, among them «0 Authorization
Framework D.» and «Internet Engineering Task Force (IETF) D.». `learned_conclusion`
already kept web conclusions out; the knowledge pipeline was the other door.

The claim still lands in the source registry (audit, qualification); a
deliberate ingestion (`core/ingestion.py`) still admits it.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.knowledge_pipeline import KnowledgePipeline

_SENTENCE = "The OAuth 2.0 authorization framework enables third-party applications to obtain limited access."


def _chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="web_page", source_id="web_page:https://www.rfc-editor.org/info/rfc6749/",
                            obtained_via="web_fetch", claim="Fetched page", excerpt=_SENTENCE, confidence=0.9))
    chain.add(make_evidence(kind="file", source_id="file:docs/oauth.md", obtained_via="file_read",
                            claim="Read file", excerpt="Our service rotates refresh tokens every thirty days.",
                            confidence=0.9))
    return chain


def _run(admit_internet: bool):
    written: list[str] = []

    def remember(content, _tags, _source, _kind, _owner):
        written.append(content)
        decision = type("Decision", (), {"decision": "save", "reasons": (), "policy_id": "test"})()
        return decision, type("Rec", (), {"id": f"mem_{len(written)}"})()

    result = KnowledgePipeline().run(_chain(), remember=remember, auto_write_memory=True,
                                     admit_internet=admit_internet)
    return result, written


def test_a_turn_keeps_web_claims_in_the_registry_only() -> None:
    result, written = _run(admit_internet=False)
    assert not any("OAuth" in w for w in written), "a web page became a durable fact"
    assert any("refresh tokens" in w for w in written), "the local world must still be learned"
    two_worlds = [d for d in result.decisions if d["knowledge_decision"].get("policy_id") == "two_worlds"]
    assert two_worlds, "the refusal must be a decision row with its rule named"
    assert any("rfc-editor" in c.source_id for c in result.registry.claims), "kept in the registry"


def test_a_deliberate_ingestion_still_admits_the_web() -> None:
    _result, written = _run(admit_internet=True)
    assert any("OAuth" in w for w in written)


def test_the_turn_is_wired_to_refuse(tmp_path) -> None:
    """The pipeline's switch is only half; the turn must throw it. Driven, not grepped:
    `_catalogue_chain` is called on a stub host and the switch it passes is read."""
    from core.loop_evidence_chain import AgentLoopEvidenceChain

    seen = {}

    class _Pipeline:
        def run(self, chain, **kw):
            seen.update(kw)
            return None

    host = AgentLoopEvidenceChain.__new__(AgentLoopEvidenceChain)
    host.knowledge_pipeline = _Pipeline()
    host.source_registry_store = None
    host.knowledge_auto_write = True
    host._knowledge_remember_batch = lambda: (lambda *a: None)
    host._unattended_run = lambda: False
    host._catalogue_chain(_chain(), question="q", may_knowledge=True, may_source_registry=False)
    assert seen.get("admit_internet") is False
