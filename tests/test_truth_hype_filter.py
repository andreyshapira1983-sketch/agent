"""Tests for core.truth_hype_filter — the truth/hype LEARNING antibody."""
from __future__ import annotations

from core.truth_hype_filter import (
    TruthHypeOutcome,
    TruthHypeSignals,
    evaluate,
    is_hype,
)


class TestVerdict:
    def test_pure_marketing_is_hype(self):
        out = evaluate(
            "Our revolutionary, game-changing platform delivers seamless, "
            "world-class synergy!"
        )
        assert out.verdict == "hype"
        assert out.is_hype is True
        assert out.hype_score >= 0.34
        assert out.substance_score <= 0.25
        assert out.signals.hype_terms  # named the offending terms

    def test_russian_marketing_is_hype(self):
        out = evaluate(
            "Революционный прорывной продукт, не имеющий аналогов — лучший в мире!"
        )
        assert out.verdict == "hype"

    def test_technical_causal_text_is_substantive(self):
        out = evaluate(
            "The deployment script fails because the DB_URL variable is not set."
        )
        assert out.verdict == "substantive"
        assert out.is_substantive is True
        assert out.signals.has_causal is True

    def test_text_with_number_and_citation_is_substantive(self):
        out = evaluate(
            "Python 3.11 reduced startup time by 25% according to the release notes."
        )
        assert out.verdict == "substantive"
        assert out.signals.has_number is True
        assert out.signals.has_citation is True

    def test_confident_but_checkable_text_is_not_dropped(self):
        # One stray buzzword inside a long, concrete, checkable sentence must
        # NOT flip it to hype — substance protects it.
        out = evaluate(
            "The cutting-edge parser in module core.loop processes 1200 tokens "
            "per second because it caches the compiled grammar in memory."
        )
        assert out.verdict == "substantive"

    def test_neutral_short_fact_is_substantive(self):
        out = evaluate("Agent mode is local.")
        assert out.verdict == "substantive"


class TestEdgeCases:
    def test_empty_input_is_substantive(self):
        out = evaluate("")
        assert out.verdict == "substantive"
        assert out.hype_score == 0.0

    def test_whitespace_input_is_substantive(self):
        assert evaluate("   \n\t ").verdict == "substantive"

    def test_non_string_input_is_substantive(self):
        assert evaluate(None).verdict == "substantive"  # type: ignore[arg-type]

    def test_long_input_is_truncated_safely(self):
        out = evaluate("word " * 5000 + "revolutionary game-changing seamless")
        assert isinstance(out, TruthHypeOutcome)

    def test_is_hype_convenience(self):
        assert is_hype("unprecedented best-in-class ultimate seamless magical!") is True
        assert is_hype("The function returns the parsed config dict.") is False


class TestDeterminismAndSerialisation:
    def test_deterministic(self):
        text = "Revolutionary seamless world-class synergy!"
        assert evaluate(text).to_dict() == evaluate(text).to_dict()

    def test_outcome_serialises(self):
        out = evaluate("Our unprecedented, game-changing, world-class platform!")
        d = out.to_dict()
        assert d["verdict"] == "hype"
        assert 0.0 <= d["hype_score"] <= 1.0
        assert 0.0 <= d["substance_score"] <= 1.0
        assert isinstance(d["signals"], dict)
        assert isinstance(d["reasons"], list)

    def test_signals_substance_markers_count(self):
        sig = TruthHypeSignals(
            has_number=True, has_proper_noun=True, has_citation=True
        )
        assert sig.substance_markers == 3
