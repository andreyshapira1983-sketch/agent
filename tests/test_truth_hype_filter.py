"""Tests for core.truth_hype_filter — the truth/hype LEARNING antibody."""
from __future__ import annotations

from core.truth_hype_filter import (
    evaluate,
    is_hype,
)


class TestVerdict:
    def test_technical_causal_text_is_substantive(self):
        out = evaluate(
            "The deployment script fails because the DB_URL variable is not set."
        )
        assert out.verdict == "substantive"
        assert out.is_substantive is True
        assert out.signals.has_causal is True


class TestEdgeCases:
    def test_whitespace_input_is_substantive(self):
        assert evaluate("   \n\t ").verdict == "substantive"

    def test_non_string_input_is_substantive(self):
        assert evaluate(None).verdict == "substantive"  # type: ignore[arg-type]


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
