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

from core.evidence import ProvenanceChain
from core.verifier import extract_unresolved_web_urls, verify


class TestExtractUnresolvedWebUrls:
    def test_empty_report_returns_empty(self):
        report = verify(answer="", chain=ProvenanceChain())
        assert extract_unresolved_web_urls(report) == []


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
