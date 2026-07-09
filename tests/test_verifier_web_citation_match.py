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
    def test_query_citation_resolves_with_search_hit_only(self):
        chain = ProvenanceChain()
        chain.add(_query_hit("python async testing"))
        ev = match_citation(_cit("python async testing"), chain)
        assert ev is not None
        assert ev.kind == "web_search_hit"

    def test_query_citation_resolves_when_chain_also_has_url_page(self):
        # The bug: a single web_page (from web_fetch) was hiding all
        # web_search_hit candidates, so [web:<query>] citations died.
        chain = ProvenanceChain()
        chain.add(_page("https://news.ycombinator.com/newest"))
        chain.add(_query_hit("python async testing"))
        chain.add(_query_hit("developer tools saas pricing"))
        ev = match_citation(_cit("python async testing"), chain)
        assert ev is not None
        assert ev.kind == "web_search_hit"
        assert "python async testing" in ev.source_id

    def test_url_citation_still_resolves_to_page(self):
        chain = ProvenanceChain()
        chain.add(_page("https://news.ycombinator.com/newest"))
        chain.add(_query_hit("hn newest"))
        ev = match_citation(
            _cit("https://news.ycombinator.com/newest"), chain
        )
        assert ev is not None
        assert ev.kind == "web_page"

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

    def test_no_web_search_hit_kind_falls_back_cleanly(self):
        # When chain has only a page and the body is unrelated, returning
        # None is the correct, conservative outcome.
        chain = ProvenanceChain()
        chain.add(_page("https://news.ycombinator.com/newest"))
        ev = match_citation(_cit("totally unrelated query"), chain)
        assert ev is None

    def test_url_body_does_not_token_match_search_hit(self):
        # Regression guard: when the citation body is itself a URL and
        # no `web_page` substring-matches it, we MUST NOT spuriously
        # attribute the citation to a `web_search_hit` whose query
        # happens to share a single word with the URL path. URL token
        # fallback is too noisy ("https", "com", TLDs) to trust.
        chain = ProvenanceChain()
        chain.add(_query_hit("ai_agent"))
        # Body is a URL; query hit shares the token "agent" with the
        # URL path but is a totally different source.
        cit = _cit("https://example.com/agent")
        ev = match_citation(cit, chain)
        # No web_page → expected_kind candidates is empty;
        # web_search_hit substring of "https://example.com/agent" against
        # "web_search:ai_agent" is False; URL-body token fallback is
        # blocked. Result: None (which the unresolved-citation replan
        # path then handles).
        assert ev is None
