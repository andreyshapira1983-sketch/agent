"""Unit tests for structured fact extraction.

Pin the contract: tool outputs serialise to text via repr() / json,
the extractor parses them back into a normalised StructuredFacts
bundle, and claim_supported_by accepts paraphrases that respect
locale (en + ru) and word boundaries.
"""
from __future__ import annotations

from core.structured_facts import (
    StructuredFacts,
    claim_supported_by,
    extract_facts,
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestExtractFacts:
    def test_garbage_returns_empty(self):
        assert extract_facts("hello world").is_empty()
        assert extract_facts("").is_empty()
        assert extract_facts("not parseable {{{").is_empty()


    def test_boolean_true_false(self):
        facts = extract_facts({"success": True, "failed": False})
        assert "true" in facts.booleans
        assert "false" in facts.booleans

    def test_string_boolean_synonyms(self):
        facts = extract_facts({"status": "OK", "result": "FAILED"})
        assert "true" in facts.booleans
        assert "false" in facts.booleans


# ---------------------------------------------------------------------------
# Claim matching
# ---------------------------------------------------------------------------

class TestClaimSupportedBy:
    def test_word_boundary_prevents_short_match(self):
        facts = extract_facts({"weekday": "Wednesday", "day": 3, "month": 6, "year": 2026})
        # "ср" is the russian abbreviation for среда — must NOT match
        # the inside of "среда" via accidental substring of a different
        # lemma. We check this by verifying the abbreviation matches
        # only standalone, not glued.
        assert claim_supported_by("Сегодня ср.", facts)
        # "среда" is a distinct token; test that pure abbreviation
        # doesn't fire on unrelated word like "среди":
        assert not claim_supported_by("Среди прочего, погода была хорошая.", facts)

    def test_empty_facts_never_supports(self):
        assert not claim_supported_by(
            "Anything goes.", StructuredFacts.empty()
        )

    def test_boolean_claim_match(self):
        facts = extract_facts({"healthy": True})
        assert claim_supported_by("System reports true.", facts)
        assert not claim_supported_by("System reports false.", facts)

    def test_number_must_match_meaningfully(self):
        # 100 is a valid numeric fact (≥3 chars); "1" is not.
        facts = extract_facts({"max": 1, "items": 100})
        assert claim_supported_by("There are 100 items here.", facts)
        # "1" is filtered, so claims about "1" don't match.
        assert not claim_supported_by("There is 1 item here.", facts)
