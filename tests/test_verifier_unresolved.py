"""MVP-14.5 — unit tests for `extract_unresolved_web_urls`.

This helper feeds the `FailureType.unresolved_citation` re-plan trigger.
Tests pin:

  * only `cited_but_unmatched` chunks contribute URLs;
  * only `[web:...]` citations contribute (search/file/test/log/etc. are
    not fetchable through web_fetch);
  * only http/https URLs pass the filter (no scheme-less hints, no
    placeholder tokens like "<best URL from search>");
  * order is preserved as the LLM cited them;
  * duplicates are de-duplicated stably.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import extract_unresolved_web_urls, verify


def _file_ev(path: str = "doc.txt"):
    return make_evidence(
        kind="file",
        source_id=path,
        obtained_via="file_read",
        claim=f"contents of {path}",
        excerpt=f"contents of {path}",
    )


def _web_ev(url: str):
    return make_evidence(
        kind="web_page",
        source_id=url,
        obtained_via="web_fetch",
        claim="page text",
        excerpt="page text",
    )


class TestExtractUnresolvedWebUrls:
    def test_empty_report_returns_empty(self):
        report = verify(answer="", chain=ProvenanceChain())
        assert extract_unresolved_web_urls(report) == []

    def test_verified_web_chunk_yields_no_url(self):
        """A [web:URL] that DID match must not appear — it's resolved."""
        chain = ProvenanceChain()
        chain.add(_web_ev("https://example.com/a"))
        report = verify(
            answer="Foo bar [web:https://example.com/a].",
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert extract_unresolved_web_urls(report) == []

    def test_single_unresolved_url(self):
        chain = ProvenanceChain()  # no web_page evidence
        report = verify(
            answer="Foo bar [web:https://example.com/x].",
            chain=chain,
        )
        assert report.cited_but_unmatched_chunks == 1
        assert extract_unresolved_web_urls(report) == [
            "https://example.com/x"
        ]

    def test_multiple_unresolved_preserve_order(self):
        chain = ProvenanceChain()
        answer = (
            "First [web:https://a.example].\n"
            "Then [web:https://b.example].\n"
            "Last [web:https://c.example]."
        )
        report = verify(answer=answer, chain=chain)
        assert extract_unresolved_web_urls(report) == [
            "https://a.example",
            "https://b.example",
            "https://c.example",
        ]

    def test_deduplicates_same_url_stably(self):
        chain = ProvenanceChain()
        answer = (
            "Once [web:https://dup.example].\n"
            "Twice [web:https://dup.example].\n"
            "Other [web:https://other.example]."
        )
        urls = extract_unresolved_web_urls(verify(answer=answer, chain=chain))
        assert urls == ["https://dup.example", "https://other.example"]

    def test_http_scheme_allowed(self):
        chain = ProvenanceChain()
        report = verify(answer="x [web:http://plain.example].", chain=chain)
        assert extract_unresolved_web_urls(report) == [
            "http://plain.example"
        ]

    def test_scheme_less_body_filtered(self):
        """Body like `Wikipedia` or `example.com` is not a fetchable URL."""
        chain = ProvenanceChain()
        report = verify(
            answer=(
                "From [web:Wikipedia] and [web:example.com] but also "
                "real [web:https://real.example]."
            ),
            chain=chain,
        )
        urls = extract_unresolved_web_urls(report)
        assert urls == ["https://real.example"]

    def test_empty_body_filtered(self):
        chain = ProvenanceChain()
        # `[web:]` would parse with body="" — must not produce a URL.
        # Note: the regex requires non-empty body after the colon, so
        # `[web:]` is actually NOT a match. The closer real case is
        # whitespace-only body, also filtered.
        report = verify(
            answer="x [web: ] y [web:https://ok.example].",
            chain=chain,
        )
        urls = extract_unresolved_web_urls(report)
        assert urls == ["https://ok.example"]

    def test_other_citation_kinds_ignored(self):
        """search/file/test/log citations are NOT web URLs and must be
        skipped — web_fetch cannot resolve them."""
        chain = ProvenanceChain()
        report = verify(
            answer=(
                "From [file:doc.txt] and [search:python docs] and "
                "[test:pytest] and [log:trace-1]."
            ),
            chain=chain,
        )
        # All four are cited_but_unmatched (no chain) but none are web.
        assert report.cited_but_unmatched_chunks >= 1
        assert extract_unresolved_web_urls(report) == []

    def test_self_declared_not_extracted(self):
        chain = ProvenanceChain()
        report = verify(
            answer="From [general-knowledge].",
            chain=chain,
        )
        # Verdict is `self_declared`, not `cited_but_unmatched`.
        assert report.self_declared_chunks == 1
        assert extract_unresolved_web_urls(report) == []

    def test_unverified_chunk_no_citation_not_extracted(self):
        """A chunk without any citation can't contribute a URL."""
        chain = ProvenanceChain()
        report = verify(
            answer="Just a plain claim with no citations.",
            chain=chain,
        )
        assert report.unverified_chunks == 1
        assert extract_unresolved_web_urls(report) == []

    def test_mixed_chunks_only_unmatched_web_contribute(self):
        chain = ProvenanceChain()
        chain.add(_file_ev("doc.txt"))
        chain.add(_web_ev("https://known.example"))
        answer = (
            "Verified file [file:doc.txt].\n"
            "Verified web [web:https://known.example].\n"
            "Unverified plain claim with no citation.\n"
            "Cited but unmatched [web:https://unknown.example].\n"
            "Self declared [general-knowledge]."
        )
        report = verify(answer=answer, chain=chain)
        urls = extract_unresolved_web_urls(report)
        assert urls == ["https://unknown.example"]

    def test_url_with_path_and_query_preserved(self):
        chain = ProvenanceChain()
        url = "https://example.com/path?q=1&r=2#frag"
        report = verify(answer=f"x [web:{url}].", chain=chain)
        assert extract_unresolved_web_urls(report) == [url]

    def test_uppercase_scheme_allowed(self):
        """Scheme check is case-insensitive — `HTTPS://...` still
        passes the gate (planner sanitiser normalises later)."""
        chain = ProvenanceChain()
        report = verify(
            answer="x [web:HTTPS://upper.example].",
            chain=chain,
        )
        assert extract_unresolved_web_urls(report) == [
            "HTTPS://upper.example"
        ]
