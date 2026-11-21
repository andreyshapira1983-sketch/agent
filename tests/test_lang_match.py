"""Tests for the language-aware, token-boundary term matcher (core.lang_match).

These focus on Russian morphology, which is where naive substring matching
falls apart.
"""

from core.lang_match import (
    STEM_MIN,
    any_term_matches,
    term_matches,
)


class TestTokenBoundaryMatching:
    def test_stem_floor_blocks_short_prefix(self):
        # A term token shorter than STEM_MIN only matches whole tokens.
        assert STEM_MIN >= 3
        assert term_matches("своё дело", "сво") is False  # "сво" is a prefix but too short

    def test_multiword_term_requires_consecutive_tokens(self):
        assert term_matches("долговременной памяти", "долговременной памят") is True
        assert term_matches("память долговременная", "долговременной памят") is False

    def test_any_term_matches(self):
        terms = ("langchain", "в интернете")
        assert any_term_matches("посмотри в интернете", terms) is True
        assert any_term_matches("просто вопрос", terms) is False
