"""Web citation matching with mixed web_page + web_search_hit chains.

Regression: when `web_fetch` adds a single URL-shaped `web_page`
record to the chain, the previous `match_citation` fallback path
required the candidate set to be EMPTY before falling back to
`web_search_hit`. As a result, every `[web:<query>]` citation
silently failed because the body never substring-matched the URL,
and the URL-prefix gate skipped token fallback. This file verifies
that the merged-candidates path now resolves both shapes.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import Citation, match_citation


def _query_hit(query: str):
    return make_evidence(
        kind="web_search_hit",
        source_id=f"web_search:{query}",
        obtained_via="web_search",
        claim=f"Search for '{query}' returned hits",
        excerpt=f"results for {query}",
    )


def _page(url: str):
    return make_evidence(
        kind="web_page",
        source_id=f"web_page:{url}",
        obtained_via="web_fetch",
        claim=f"Fetched {url}",
        excerpt="page body",
    )


def _cit(body: str) -> Citation:
    return Citation(
        prefix="web",
        body=body,
        raw=f"[web:{body}]",
        expected_kind="web_page",
    )


class TestWebCitationMixedChain:
    def test_unmatched_query_does_not_steal_a_page(self):
        # If the body does not substring-match any source_id and has
        # no token overlap, we still refuse to match — better an honest
        # cited_but_unmatched than a wrong attribution.
        chain = ProvenanceChain()
        chain.add(_page("https://example.com/foo"))
        chain.add(_query_hit("apple banana cherry"))
        ev = match_citation(_cit("zzz totally unrelated query"), chain)
        assert ev is None

    def test_token_fallback_recovers_loose_paraphrase(self):
        # Body uses different word order / extra tokens but shares two
        # meaningful tokens with the search source_id — token-overlap
        # pass should still pick the right hit.
        chain = ProvenanceChain()
        chain.add(_page("https://example.com/unrelated"))
        chain.add(_query_hit("python async testing race conditions"))
        ev = match_citation(_cit("testing python deadlock race"), chain)
        assert ev is not None
        assert ev.kind == "web_search_hit"
        assert "race conditions" in ev.source_id
