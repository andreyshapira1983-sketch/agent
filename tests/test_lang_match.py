"""Tests for the language-aware, token-boundary term matcher (core.lang_match).

These focus on Russian morphology, which is where naive substring matching
falls apart.
"""

from core.lang_match import (
    STEM_MIN,
    any_term_matches,
    normalize_text,
    term_matches,
    tokenize,
)


class TestNormalizeAndTokenize:
    def test_casefold_and_yo_folding(self):
        assert normalize_text("СВОЁ") == "свое"
        assert normalize_text("Твоём") == "твоем"

    def test_tokenize_splits_on_punctuation_and_hyphen(self):
        assert tokenize("суб-агент, привет!") == ("суб", "агент", "привет")

    def test_tokenize_unifies_yo(self):
        assert tokenize("своё") == tokenize("свое") == ("свое",)


class TestTokenBoundaryMatching:
    def test_short_preposition_does_not_match_inside_longer_word(self):
        # The production bug: "с" must not match inside "своё".
        assert term_matches("сравни своё поведение", "с") is False
        assert term_matches("весь мир интересен", "в") is False

    def test_exact_short_token_still_matches(self):
        assert term_matches("сравни с прошлым", "с") is True
        assert term_matches("в интернете", "в") is True

    def test_stem_prefix_matches_inflections(self):
        for text in ("репозиторий", "репозитория", "репозитории", "репозиториях"):
            assert term_matches(f"в моём {text}", "репозитор") is True

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
