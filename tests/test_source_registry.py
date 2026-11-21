"""Source Registry tests.

This pins the catalog layer above Evidence: source records and extracted
claims are separate objects, so books/PDFs/docs/logs/tests/user notes can all
feed the same knowledge pipeline later.
"""

from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.source_ranker import rank_chain
from core.source_registry import SourceRegistry, source_type_from_evidence


def _ev(kind: str, source: str, *, claim: str = "claim"):
    return make_evidence(
        kind=kind,  # type: ignore[arg-type]
        source_id=source,
        obtained_via="test",
        claim=claim,
        excerpt="excerpt",
        confidence=0.8,
    )


def test_manual_book_source_can_store_page_level_claim():
    registry = SourceRegistry()
    source = registry.register_source(
        type="book",
        title="Artificial Intelligence: A Modern Approach",
        locator="chapter=2 page=34",
        author="Russell and Norvig",
        trust_level=0.86,
    )

    claim = registry.register_claim(
        source_id=source.id,
        text="An agent perceives through sensors and acts through actuators.",
        locator="chapter=2 page=34",
        confidence=0.82,
        status="verified",
    )

    assert source.type == "book"
    assert claim.source_id == source.id
    assert registry.claims_for_source(source.id) == (claim,)
    payload = registry.to_log_payload()
    assert payload["source_count"] == 1
    assert payload["claim_count"] == 1
    assert payload["source_types"]["book"] == 1
    assert payload["claim_statuses"]["verified"] == 1


def test_source_type_from_evidence_maps_core_evidence_kinds():
    assert source_type_from_evidence(_ev("file", "file:README.md")) == "file"
    assert source_type_from_evidence(_ev("test_result", "test:pytest tests")) == "test_result"
    assert source_type_from_evidence(_ev("log_event", "log:trace")) == "log"
    assert source_type_from_evidence(_ev("memory", "memory:mem_123")) == "memory"
    assert source_type_from_evidence(_ev("user_explicit", "user:req_123")) == "user"
    docs = _ev("web_page", "web_page:https://docs.python.org/3/library/json.html")
    assert source_type_from_evidence(docs) == "documentation"
    forum = _ev("web_page", "web_page:https://reddit.com/r/python/comments/1")
    assert source_type_from_evidence(forum) == "forum"
    rss = make_evidence(
        kind="web_page",
        source_id="web_page:https://example.com/feed.xml",
        obtained_via="rss_fetch",
        claim="RSS feed",
        excerpt="entry",
        confidence=0.68,
    )
    assert source_type_from_evidence(rss) == "article"


def test_registry_from_provenance_uses_ranking_metadata():
    chain = ProvenanceChain()
    chain.add(_ev("web_search_hit", "web_search:bitcoin price", claim="search pointer"))
    chain.add(_ev("file", "file:README.md", claim="README says x"))
    ranking = rank_chain(chain, question="Какая цена Bitcoin прямо сейчас?")

    registry = SourceRegistry.from_provenance(chain, ranking=ranking)

    assert len(registry.sources) == 2
    assert len(registry.claims) == 2
    search_source = registry.get_source("web_search:bitcoin price")
    assert search_source is not None
    assert search_source.metadata["rank_tier"] == "search_pointer"
    assert search_source.trust_level <= 0.35
    search_claim = registry.claims_for_source("web_search:bitcoin price")[0]
    assert search_claim.status == "unverified"
    file_source = registry.get_source("file:README.md")
    assert file_source is not None
    assert file_source.type == "file"
