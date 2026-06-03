"""MVP-14.4 — unit tests for the Verifier.

These pin the contract Verifier presents to the rest of the loop:

  * every claim either resolves to an Evidence record (verified) or
    gets an [unverified] tag — there is no third state where a claim
    silently sneaks through;
  * a fully unverified answer ALWAYS carries a disclaimer;
  * citation grammar parsing is deterministic and bracket-safe;
  * matching is substring-on-source_id with same-kind preference.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    CITATION_PREFIXES,
    DISCLAIMER_ALL_SELF_DECLARED,
    DISCLAIMER_FULLY_UNVERIFIED,
    DISCLAIMER_NO_CHAIN,
    DISCLAIMER_SESSION_MEMORY,
    SELF_DECLARED_PREFIXES,
    Citation,
    ClaimChunk,
    VerificationReport,
    is_structural_chunk,
    match_citation,
    parse_citations,
    split_into_chunks,
    verify,
)


# ============================================================
# Citation grammar parsing
# ============================================================

class TestParseCitations:
    def test_simple_file_citation(self):
        cits = parse_citations("Hello [file:foo.txt] world.")
        assert len(cits) == 1
        c = cits[0]
        assert c.prefix == "file"
        assert c.body == "foo.txt"
        assert c.expected_kind == "file"
        assert c.raw == "[file:foo.txt]"

    def test_multiple_citations(self):
        text = "[file:a.txt] and [web:https://example.com] and [user]"
        cits = parse_citations(text)
        assert [c.prefix for c in cits] == ["file", "web", "user"]
        assert cits[1].body == "https://example.com"
        assert cits[2].body == ""

    def test_user_citation_has_no_body(self):
        cits = parse_citations("User said so [user].")
        assert len(cits) == 1
        assert cits[0].prefix == "user"
        assert cits[0].body == ""
        assert cits[0].expected_kind == "user_explicit"

    def test_no_citations(self):
        assert parse_citations("Just text without anything.") == []

    def test_malformed_brackets_not_a_citation(self):
        # `[unknown:foo]` is not in the grammar — must not parse.
        assert parse_citations("see [unknown:foo]") == []

    def test_nested_brackets_safe(self):
        # The citation regex stops at `]` — so it won't engulf nested
        # content that's not a real citation.
        cits = parse_citations("This [file:foo.txt] is [not a citation]")
        assert len(cits) == 1
        assert cits[0].body == "foo.txt"

    def test_every_prefix_recognised(self):
        text = " ".join(f"[{p}:x]" for p in CITATION_PREFIXES.keys())
        cits = parse_citations(text)
        assert {c.prefix for c in cits} == set(CITATION_PREFIXES.keys())

    def test_citation_with_url_body(self):
        cits = parse_citations("[web:https://example.com/path?q=1]")
        assert len(cits) == 1
        assert cits[0].body == "https://example.com/path?q=1"

    def test_citation_with_colon_in_body(self):
        cits = parse_citations("[test:python -m pytest -k memory]")
        assert len(cits) == 1
        assert cits[0].body == "python -m pytest -k memory"


# ============================================================
# Sentence splitting
# ============================================================

class TestSplit:
    def test_period_split(self):
        out = split_into_chunks("First. Second. Third.")
        assert out == ["First.", "Second.", "Third."]

    def test_question_mark_split(self):
        out = split_into_chunks("Is this true? Yes!")
        assert out == ["Is this true?", "Yes!"]

    def test_newline_split(self):
        out = split_into_chunks("Line A\nLine B")
        assert out == ["Line A", "Line B"]

    def test_empty_chunks_dropped(self):
        assert split_into_chunks("") == []
        assert split_into_chunks("   ") == []

    def test_markdown_list(self):
        out = split_into_chunks("- alpha\n- beta\n- gamma")
        assert len(out) == 3


# ============================================================
# match_citation
# ============================================================

def _chain_with(*evidences):
    c = ProvenanceChain()
    for ev in evidences:
        c.add(ev)
    return c


class TestMatchCitation:
    def test_file_citation_matches_file_evidence(self):
        ev = make_evidence(
            kind="file", source_id="file:foo.txt", obtained_via="file_read",
            claim="x", excerpt="hello",
        )
        chain = _chain_with(ev)
        cit = Citation(prefix="file", body="foo.txt", raw="[file:foo.txt]",
                       expected_kind="file")
        assert match_citation(cit, chain) is ev

    def test_wrong_kind_no_match(self):
        """`[file:foo.txt]` must not match a `web_page` evidence even if
        the source_id substring happens to overlap."""
        ev = make_evidence(
            kind="web_page", source_id="web_page:https://example.com/foo.txt",
            obtained_via="web_fetch", claim="x", excerpt="y",
        )
        chain = _chain_with(ev)
        cit = Citation(prefix="file", body="foo.txt", raw="[file:foo.txt]",
                       expected_kind="file")
        assert match_citation(cit, chain) is None

    def test_user_citation_picks_user_explicit(self):
        ev = make_evidence(
            kind="user_explicit", source_id="user_explicit:req_42",
            obtained_via="user_explicit", claim="x", excerpt="",
        )
        chain = _chain_with(ev)
        cit = Citation(prefix="user", body="", raw="[user]",
                       expected_kind="user_explicit")
        assert match_citation(cit, chain) is ev

    def test_partial_substring_match(self):
        ev = make_evidence(
            kind="web_page",
            source_id="web_page:https://docs.python.org/3/library/os.html",
            obtained_via="web_fetch", claim="x", excerpt="y",
        )
        chain = _chain_with(ev)
        cit = Citation(prefix="web", body="docs.python.org",
                       raw="[web:docs.python.org]", expected_kind="web_page")
        assert match_citation(cit, chain) is ev

    def test_empty_chain(self):
        cit = Citation(prefix="file", body="x", raw="[file:x]",
                       expected_kind="file")
        assert match_citation(cit, ProvenanceChain()) is None

    def test_first_kind_match_returned_when_no_body(self):
        ev1 = make_evidence(
            kind="user_explicit", source_id="user_explicit:req_1",
            obtained_via="user_explicit", claim="x", excerpt="",
        )
        ev2 = make_evidence(
            kind="user_explicit", source_id="user_explicit:req_2",
            obtained_via="user_explicit", claim="x", excerpt="",
        )
        chain = _chain_with(ev1, ev2)
        cit = Citation(prefix="user", body="", raw="[user]",
                       expected_kind="user_explicit")
        # `by_kind` returns the list in chain order; `[0]` is the first.
        assert match_citation(cit, chain) is ev1


# ============================================================
# verify() — overall behaviour
# ============================================================

def _file_ev(path: str, excerpt: str = "content") -> object:
    return make_evidence(
        kind="file", source_id=f"file:{path}", obtained_via="file_read",
        claim=f"file {path}", excerpt=excerpt,
    )


class TestVerifyHappyPath:
    def test_single_verified_claim(self):
        chain = _chain_with(_file_ev("foo.txt", "hello"))
        report = verify(
            answer="The file says hello [file:foo.txt].",
            chain=chain,
        )
        assert report.total_chunks == 1
        assert report.verified_chunks == 1
        assert report.unverified_chunks == 0
        assert report.fully_unverified is False
        assert "[verified:file:foo.txt]" in report.annotated_answer
        assert report.disclaimer is None

    def test_unverified_claim_gets_tag(self):
        report = verify(
            answer="The earth is round.",
            chain=_chain_with(_file_ev("foo.txt")),
        )
        assert report.unverified_chunks == 1
        assert report.verified_chunks == 0
        assert "[unverified]" in report.annotated_answer
        # Disclaimer fires because EVERY chunk is unverified AND chain
        # wasn't empty (the agent had sources it failed to cite).
        assert report.disclaimer == DISCLAIMER_FULLY_UNVERIFIED
        assert DISCLAIMER_FULLY_UNVERIFIED in report.annotated_answer


class TestVerifyMixedAnswer:
    def test_mix_of_verified_and_unverified(self):
        chain = _chain_with(_file_ev("a.txt"))
        answer = "Fact A is in a.txt [file:a.txt]. Fact B is unsupported."
        report = verify(answer=answer, chain=chain)
        assert report.total_chunks == 2
        assert report.verified_chunks == 1
        assert report.unverified_chunks == 1
        # Mixed answer is NOT fully_unverified — disclaimer not added.
        assert report.fully_unverified is False
        assert report.disclaimer is None
        # First chunk has the rewritten citation, second has [unverified].
        assert "[verified:file:a.txt]" in report.annotated_answer
        assert "[unverified]" in report.annotated_answer


class TestVerifyCitedButUnmatched:
    def test_citation_to_nonexistent_source_flagged(self):
        chain = _chain_with(_file_ev("a.txt"))
        report = verify(
            answer="See [file:nope.txt] for details.",
            chain=chain,
        )
        assert report.cited_but_unmatched_chunks == 1
        assert report.verified_chunks == 0
        # Raw citation stays in answer (it's unrewritten on purpose).
        assert "[file:nope.txt]" in report.annotated_answer
        # No "verified:" prefix anywhere.
        assert "[verified:" not in report.annotated_answer
        # Cited-but-unmatched DOES count as "no verified claims" -> disclaimer.
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_FULLY_UNVERIFIED


class TestVerifyEmptyChain:
    def test_no_chain_with_unverified_answer_gets_no_chain_note(self):
        report = verify(
            answer="General knowledge answer.",
            chain=ProvenanceChain(),
        )
        assert report.chain_was_empty is True
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_NO_CHAIN
        assert DISCLAIMER_NO_CHAIN in report.annotated_answer

    def test_no_chain_with_citation_gets_no_chain_note(self):
        """A citation to a non-existent source still triggers the
        chain_was_empty disclaimer (not the fully_unverified one)."""
        report = verify(
            answer="See [file:foo.txt].",
            chain=ProvenanceChain(),
        )
        assert report.chain_was_empty is True
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_NO_CHAIN

    def test_memory_citation_empty_chain_session_memory_disclaimer(self):
        """When the agent answers from Working Memory (no tools ran, chain
        is empty) and all unmatched citations use the `memory:` prefix,
        the disclaimer should be DISCLAIMER_SESSION_MEMORY — not the
        misleading DISCLAIMER_NO_CHAIN — because the answer IS grounded
        in the agent’s prior-turn history."""
        report = verify(
            answer=(
                "The tests ran in turn 3 [memory:turn_3_test_results]. "
                "Two failures were found [memory:turn_3_failures]."
            ),
            chain=ProvenanceChain(),
        )
        assert report.chain_was_empty is True
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_SESSION_MEMORY
        assert DISCLAIMER_SESSION_MEMORY in report.annotated_answer
        # The misleading “no sources” note must NOT appear.
        assert DISCLAIMER_NO_CHAIN not in report.annotated_answer

    def test_mixed_memory_and_file_citation_empty_chain_no_chain_disclaimer(self):
        """If even ONE unmatched citation is not a memory: citation, the
        more cautious DISCLAIMER_NO_CHAIN is used."""
        report = verify(
            answer=(
                "Result [memory:turn_3_data]. Also see [file:missing.txt]."
            ),
            chain=ProvenanceChain(),
        )
        assert report.disclaimer == DISCLAIMER_NO_CHAIN

    def test_memory_citation_nonempty_chain_session_memory_disclaimer(self):
        """Regression: even when the chain has Working Memory evidence
        injected (chain_was_empty=False), if all cited-but-unmatched
        chunks reference memory: prefix the disclaimer should still be
        DISCLAIMER_SESSION_MEMORY, not DISCLAIMER_FULLY_UNVERIFIED.

        This matches the live-run scenario where loop.py injects
        Working Memory artifacts into the chain but the LLM-generated
        citation body (e.g. 'Turn_3_test_results') doesn't substring-
        match the stored source_id ('working_turn_3_run_tests_tests')."""
        from core.evidence import make_evidence
        chain = ProvenanceChain()
        # Simulate what loop.py injects: Working Memory evidence whose
        # source_id does NOT exactly match what the LLM generates.
        chain.add(make_evidence(
            kind="memory",
            source_id="memory:working_turn_3_run_tests_tests",
            obtained_via="working_memory",
            claim="Cached tool output from turn 3: run_tests:tests",
            excerpt="2494 passed in 16.34s",
            confidence=0.85,
        ))
        report = verify(
            answer=(
                "All 2494 tests passed [memory:Turn_3_test_results]. "
                "No failures found [memory:Turn_3_test_results]."
            ),
            chain=chain,
        )
        assert report.chain_was_empty is False
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_SESSION_MEMORY
        assert DISCLAIMER_FULLY_UNVERIFIED not in report.annotated_answer


class TestVerifyEmptyAnswer:
    def test_empty_answer_yields_zero_chunks(self):
        report = verify(answer="", chain=_chain_with(_file_ev("a.txt")))
        assert report.total_chunks == 0
        assert report.verified_chunks == 0
        # Empty answer is by definition fully unverified.
        assert report.fully_unverified is True

    def test_whitespace_only_answer(self):
        report = verify(answer="   \n\n  ", chain=ProvenanceChain())
        assert report.total_chunks == 0
        assert report.fully_unverified is True


# ============================================================
# Multiple citations in one chunk
# ============================================================

class TestMultipleCitationsPerChunk:
    def test_two_verified_citations_one_verdict(self):
        chain = _chain_with(_file_ev("a.txt"), _file_ev("b.txt"))
        report = verify(
            answer="Both files agree [file:a.txt][file:b.txt].",
            chain=chain,
        )
        # One chunk, both citations rewritten, verdict=verified.
        assert report.total_chunks == 1
        assert report.verified_chunks == 1
        assert "[verified:file:a.txt]" in report.annotated_answer
        assert "[verified:file:b.txt]" in report.annotated_answer
        # Both evidence IDs captured.
        assert len(report.chunks[0].matched_evidence_ids) == 2

    def test_one_matched_one_unmatched_still_verified(self):
        chain = _chain_with(_file_ev("a.txt"))
        report = verify(
            answer="Sources [file:a.txt] and [file:nope.txt].",
            chain=chain,
        )
        # Even with a bad citation, the chunk has AT LEAST one valid
        # match — verdict is "verified". The bad citation stays raw.
        assert report.verified_chunks == 1
        assert "[verified:file:a.txt]" in report.annotated_answer
        assert "[file:nope.txt]" in report.annotated_answer
        assert "[verified:file:nope.txt]" not in report.annotated_answer


# ============================================================
# Disclaimer copy stability
# ============================================================

class TestDisclaimerCopy:
    """The exact disclaimer strings are part of the user-visible
    contract. Pin them so a future change is explicit."""

    def test_fully_unverified_string(self):
        assert "not grounded in any source" in DISCLAIMER_FULLY_UNVERIFIED
        assert "prior knowledge" in DISCLAIMER_FULLY_UNVERIFIED

    def test_no_chain_string(self):
        assert "No external sources were gathered" in DISCLAIMER_NO_CHAIN
        assert "prior knowledge" in DISCLAIMER_NO_CHAIN


# ============================================================
# to_log_payload
# ============================================================

class TestLogPayload:
    def test_payload_shape(self):
        chain = _chain_with(_file_ev("a.txt"))
        report = verify(
            answer="A [file:a.txt]. B.",
            chain=chain,
        )
        payload = report.to_log_payload()
        assert payload["total_chunks"] == 2
        assert payload["verified_chunks"] == 1
        assert payload["unverified_chunks"] == 1
        assert payload["fully_unverified"] is False
        assert payload["disclaimer_set"] is False
        assert payload["verdicts"] == ["verified", "unverified"]
        # No big strings in the payload — chunks aren't serialised.
        assert "chunks" not in payload
        assert "annotated_answer" not in payload


# ============================================================
# Order preservation
# ============================================================

class TestAnnotatedAnswerStructure:
    def test_chunks_joined_with_newlines(self):
        chain = _chain_with(_file_ev("a.txt"))
        answer = "A [file:a.txt]. B."
        report = verify(answer=answer, chain=chain)
        # Two chunks → joined by newline.
        assert "\n" in report.annotated_answer

    def test_chunk_order_preserved(self):
        chain = _chain_with(_file_ev("a.txt"), _file_ev("b.txt"))
        answer = "First [file:a.txt]. Second [file:b.txt]."
        report = verify(answer=answer, chain=chain)
        idx_a = report.annotated_answer.find("[verified:file:a.txt]")
        idx_b = report.annotated_answer.find("[verified:file:b.txt]")
        assert 0 <= idx_a < idx_b


# ============================================================
# Mixed-kind chain (Verifier picks the right kind)
# ============================================================

class TestKindAffinity:
    def test_search_citation_matches_search_hit_only(self):
        search_ev = make_evidence(
            kind="web_search_hit",
            source_id="web_search:python",
            obtained_via="web_search",
            claim="search for python", excerpt="",
        )
        web_ev = make_evidence(
            kind="web_page",
            source_id="web_page:https://python.org",
            obtained_via="web_fetch",
            claim="python homepage", excerpt="...",
        )
        chain = _chain_with(search_ev, web_ev)
        # `[search:python]` must match the search_hit, NOT the web_page.
        cit = Citation(prefix="search", body="python", raw="[search:python]",
                       expected_kind="web_search_hit")
        matched = match_citation(cit, chain)
        assert matched is search_ev
        # `[web:python]` must match the web_page, NOT the search hit.
        cit = Citation(prefix="web", body="python", raw="[web:python]",
                       expected_kind="web_page")
        matched = match_citation(cit, chain)
        assert matched is web_ev


# ============================================================
# Defensive: long answers don't blow up
# ============================================================

class TestRobustness:
    def test_huge_answer_is_processed(self):
        chain = _chain_with(_file_ev("a.txt"))
        # 1000 sentences, half cited, half not.
        sents = []
        for i in range(500):
            sents.append(f"Cited fact {i} [file:a.txt].")
            sents.append(f"Bare fact {i}.")
        answer = " ".join(sents)
        report = verify(answer=answer, chain=chain)
        assert report.total_chunks == 1000
        assert report.verified_chunks == 500
        assert report.unverified_chunks == 500
        # Annotated still bounded — every chunk got exactly one edit.
        assert report.annotated_answer.count("[verified:file:a.txt]") == 500


# ============================================================
# MVP-14.4.x — Structural-chunk skipping
# ============================================================

class TestIsStructuralChunk:
    @pytest.mark.parametrize("header", [
        "Conclusion:", "Facts:", "Sources:", "Confidence:",
        "Unverified:", "Safety:",
        "conclusion:", "FACTS:",          # case-insensitive
        "Conclusion", "Facts",            # colon optional
        "  Conclusion:  ",                # whitespace tolerant
        "# Conclusion", "## Facts:",      # markdown-prefixed
        "**Conclusion:**", "**Facts:**",  # markdown-bold headers
    ])
    def test_output_contract_headers_are_structural(self, header: str):
        assert is_structural_chunk(header)

    @pytest.mark.parametrize("md", [
        "# Title", "## Subtitle", "### h3", "#### h4",
        "##### h5", "###### h6",
    ])
    def test_markdown_headings_without_citation_are_structural(self, md: str):
        assert is_structural_chunk(md)

    def test_markdown_heading_with_citation_is_NOT_structural(self):
        # A heading that itself carries a citation is a claim.
        assert not is_structural_chunk("# Result [file:x.txt]")

    @pytest.mark.parametrize("marker", [
        "-", "*", "+", "1.", "2.", "10.", "1)", "2)",
        "  -  ", "  1.  ",
    ])
    def test_bare_list_markers_are_structural(self, marker: str):
        assert is_structural_chunk(marker)

    def test_empty_is_structural(self):
        assert is_structural_chunk("")
        assert is_structural_chunk("   ")

    @pytest.mark.parametrize("real_claim", [
        "The capital of France is Paris.",
        "- Item alpha is mentioned [file:doc.txt].",
        "1. First entry from search results [search:q].",
        "Conclusion: paris is the capital [file:doc.txt].",  # header WITH content
    ])
    def test_real_claims_are_not_structural(self, real_claim: str):
        assert not is_structural_chunk(real_claim)


class TestStructuralChunksBypassVerification:
    def test_pure_structural_answer_yields_no_claims(self):
        report = verify(
            answer="# Conclusion\nFacts:\n-\nSources:\n1.",
            chain=ProvenanceChain(),
        )
        # All 5 chunks are structural -> total_chunks=0, structural=5.
        assert report.total_chunks == 0
        assert report.structural_chunks == 5
        # No [unverified] tags polluting the output.
        assert "[unverified]" not in report.annotated_answer
        # When EVERY chunk is structural we have no claims at all, so
        # the "fully_unverified" branch fires (chain is empty, too).
        assert report.fully_unverified is True
        assert report.disclaimer == DISCLAIMER_NO_CHAIN

    def test_mixed_structural_and_claims(self):
        chain = _chain_with(_file_ev("a.txt"))
        answer = (
            "Conclusion:\n"
            "Paris is the capital [file:a.txt].\n"
            "Facts:\n"
            "- alpha [file:a.txt].\n"
            "- beta.\n"
            "Sources:\n"
            "1."
        )
        report = verify(answer=answer, chain=chain)
        # Structural: Conclusion:, Facts:, Sources:, 1.  -> 4
        assert report.structural_chunks == 4
        # Claims: Paris..., - alpha [verified], - beta [unverified]  -> 3
        assert report.total_chunks == 3
        assert report.verified_chunks == 2
        assert report.unverified_chunks == 1
        # Structural headers preserved verbatim — no [unverified] on them.
        for header in ("Conclusion:", "Facts:", "Sources:", "1."):
            assert header in report.annotated_answer
        # `[unverified]` appears ONLY on the bare "beta" bullet.
        assert report.annotated_answer.count("[unverified]") == 1

    def test_output_contract_metadata_sections_are_not_claims(self):
        chain = _chain_with(_file_ev("a.txt"))
        answer = (
            "# Conclusion\n"
            "Paris is the capital [file:a.txt].\n"
            "# Facts\n"
            "- Paris is mentioned [file:a.txt].\n"
            "# Sources\n"
            "1.\n"
            "file:a.txt - source description.\n"
            "# Confidence\n"
            "high\n"
            "# Unverified\n"
            "nothing\n"
            "# Safety\n"
            "nothing"
        )

        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 2
        assert report.unverified_chunks == 0
        assert report.cited_but_unmatched_chunks == 0
        assert report.total_chunks == 2
        assert "[unverified]" not in report.annotated_answer
        assert "file:a.txt - source description." in report.annotated_answer
        assert "\nhigh\n" in report.annotated_answer
        assert report.structural_chunks >= 9

    def test_log_payload_carries_structural_count(self):
        report = verify(
            answer="Conclusion:\nfact [file:x].",
            chain=_chain_with(_file_ev("x")),
        )
        payload = report.to_log_payload()
        assert payload["structural_chunks"] == 1
        assert payload["total_chunks"] == 1


# ============================================================
# MVP-14.4.x — [general-knowledge] as self_declared
# ============================================================

class TestGeneralKnowledgePrefix:
    def test_prefix_registered(self):
        assert "general-knowledge" in CITATION_PREFIXES
        assert "general-knowledge" in SELF_DECLARED_PREFIXES
        assert CITATION_PREFIXES["general-knowledge"] == "llm_claim"

    def test_parses_as_citation(self):
        cits = parse_citations("Sky is blue [general-knowledge].")
        assert len(cits) == 1
        assert cits[0].prefix == "general-knowledge"
        assert cits[0].expected_kind == "llm_claim"

    def test_self_declared_verdict(self):
        report = verify(
            answer="The earth orbits the sun [general-knowledge].",
            chain=ProvenanceChain(),
        )
        assert report.total_chunks == 1
        assert report.self_declared_chunks == 1
        assert report.verified_chunks == 0
        assert report.unverified_chunks == 0
        # Rewritten to `[declared:general-knowledge]` — visible UX signal.
        assert "[declared:general-knowledge]" in report.annotated_answer
        assert "[unverified]" not in report.annotated_answer
        assert report.chunks[0].verdict == "self_declared"

    def test_full_self_declared_gets_dedicated_disclaimer(self):
        report = verify(
            answer=(
                "Paris is in France [general-knowledge].\n"
                "Sky is blue [general-knowledge]."
            ),
            chain=ProvenanceChain(),
        )
        assert report.self_declared_chunks == 2
        assert report.verified_chunks == 0
        # Not "fully_unverified" — model honestly admitted.
        assert report.fully_unverified is False
        assert report.disclaimer == DISCLAIMER_ALL_SELF_DECLARED
        assert DISCLAIMER_ALL_SELF_DECLARED in report.annotated_answer

    def test_mixed_verified_and_self_declared_no_disclaimer(self):
        chain = _chain_with(_file_ev("x"))
        report = verify(
            answer=(
                "From file: A [file:x].\n"
                "From training: B [general-knowledge]."
            ),
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert report.self_declared_chunks == 1
        # Neither disclaimer fires when we have BOTH types.
        assert report.disclaimer is None
        assert "[verified:file:x]" in report.annotated_answer
        assert "[declared:general-knowledge]" in report.annotated_answer

    def test_verified_beats_self_declared_in_same_chunk(self):
        """A chunk citing BOTH a real source and general-knowledge is
        verified — the real source takes precedence in the verdict."""
        chain = _chain_with(_file_ev("x"))
        report = verify(
            answer="Both [file:x] and prior knowledge [general-knowledge] agree.",
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert report.self_declared_chunks == 0
        # Both citations still rewritten in the annotated text.
        assert "[verified:file:x]" in report.annotated_answer
        assert "[declared:general-knowledge]" in report.annotated_answer

    def test_self_declared_logged(self):
        report = verify(
            answer="x [general-knowledge].",
            chain=ProvenanceChain(),
        )
        payload = report.to_log_payload()
        assert payload["self_declared_chunks"] == 1
        assert payload["disclaimer_set"] is True
        assert payload["verdicts"] == ["self_declared"]


# ============================================================
# MVP-14.4.x — Disclaimer matrix
# ============================================================

class TestDisclaimerMatrix:
    """Sanity grid: every (chain_empty, verified, self_declared,
    unverified) combination produces the correct disclaimer (or none)."""

    def test_verified_only_no_disclaimer(self):
        report = verify(
            answer="a [file:x].",
            chain=_chain_with(_file_ev("x")),
        )
        assert report.disclaimer is None

    def test_self_declared_only_with_chain(self):
        report = verify(
            answer="a [general-knowledge].",
            chain=_chain_with(_file_ev("x")),
        )
        assert report.disclaimer == DISCLAIMER_ALL_SELF_DECLARED

    def test_self_declared_only_empty_chain(self):
        report = verify(
            answer="a [general-knowledge].",
            chain=ProvenanceChain(),
        )
        assert report.disclaimer == DISCLAIMER_ALL_SELF_DECLARED

    def test_unverified_with_chain(self):
        report = verify(
            answer="a.",
            chain=_chain_with(_file_ev("x")),
        )
        assert report.disclaimer == DISCLAIMER_FULLY_UNVERIFIED

    def test_unverified_empty_chain(self):
        report = verify(answer="a.", chain=ProvenanceChain())
        assert report.disclaimer == DISCLAIMER_NO_CHAIN

    def test_disclaimer_copy_pinned(self):
        assert "honestly admits" in DISCLAIMER_ALL_SELF_DECLARED
        assert "general-knowledge" in DISCLAIMER_ALL_SELF_DECLARED
