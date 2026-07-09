"""Unit tests for structured fact extraction.

Pin the contract: tool outputs serialise to text via repr() / json,
the extractor parses them back into a normalised StructuredFacts
bundle, and claim_supported_by accepts paraphrases that respect
locale (en + ru) and word boundaries.
"""
from __future__ import annotations

import pytest

from core.structured_facts import (
    StructuredFacts,
    claim_supported_by,
    extract_facts,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestExtractFacts:
    def test_python_repr_dict_parses(self):
        excerpt = (
            "{'iso_utc': '2026-06-03T12:47:35+00:00', "
            "'weekday': 'Wednesday', 'year': 2026, 'month': 6, 'day': 3, "
            "'tz_name': 'Europe/Moscow', 'unix': 1780000000}"
        )
        facts = extract_facts(excerpt)
        assert not facts.is_empty()
        assert "2026-06-03" in facts.dates
        assert "wednesday" in facts.weekdays
        assert "среда" in facts.weekdays
        assert "2026" in facts.numbers
        assert "1780000000" in facts.numbers

    def test_json_object_parses(self):
        excerpt = '{"weekday": "Friday", "year": 2025, "month": 12, "day": 31}'
        facts = extract_facts(excerpt)
        assert "friday" in facts.weekdays
        assert "пятница" in facts.weekdays
        assert "2025-12-31" in facts.dates

    def test_garbage_returns_empty(self):
        assert extract_facts("hello world").is_empty()
        assert extract_facts("").is_empty()
        assert extract_facts("not parseable {{{").is_empty()

    def test_already_parsed_dict_accepted(self):
        facts = extract_facts({"weekday": "Monday", "day": 7, "month": 1, "year": 2026})
        assert "monday" in facts.weekdays
        assert "понедельник" in facts.weekdays
        assert "2026-01-07" in facts.dates

    def test_locale_month_names_emitted(self):
        facts = extract_facts({"year": 2026, "month": 6, "day": 3})
        # Russian and English month names with day should appear
        assert "3 июня 2026" in facts.dates
        assert "3 june 2026" in facts.dates

    def test_boolean_true_false(self):
        facts = extract_facts({"success": True, "failed": False})
        assert "true" in facts.booleans
        assert "false" in facts.booleans

    def test_string_boolean_synonyms(self):
        facts = extract_facts({"status": "OK", "result": "FAILED"})
        assert "true" in facts.booleans
        assert "false" in facts.booleans

    def test_short_numbers_filtered(self):
        # Numbers shorter than 3 chars must NOT enter the numeric set —
        # otherwise "1" or "0" would match almost any prose.
        facts = extract_facts({"count": 1, "max": 7, "items": 100})
        assert "1" not in facts.numbers
        assert "7" not in facts.numbers
        assert "100" in facts.numbers

    def test_nested_structure_walked(self):
        facts = extract_facts({
            "outer": {"inner": {"day": 15, "month": 3, "year": 2026}}
        })
        assert "2026-03-15" in facts.dates


# ---------------------------------------------------------------------------
# Claim matching
# ---------------------------------------------------------------------------

class TestClaimSupportedBy:
    def test_russian_weekday_matches_english_source(self):
        facts = extract_facts({"weekday": "Wednesday", "day": 3, "month": 6, "year": 2026})
        assert claim_supported_by(
            "Сегодня среда, 3 июня 2026 года.", facts
        )

    def test_english_weekday_matches_english_source(self):
        facts = extract_facts({"weekday": "Wednesday", "day": 3, "month": 6, "year": 2026})
        assert claim_supported_by(
            "Today is Wednesday, June 3, 2026.", facts
        )

    def test_iso_date_in_claim(self):
        facts = extract_facts({"day": 3, "month": 6, "year": 2026})
        assert claim_supported_by("The date is 2026-06-03.", facts)

    def test_unrelated_claim_rejected(self):
        facts = extract_facts({"weekday": "Wednesday", "day": 3, "month": 6, "year": 2026})
        assert not claim_supported_by(
            "Кошки спят 16 часов в сутки.", facts
        )

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
