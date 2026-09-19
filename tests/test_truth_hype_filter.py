"""Tests for core.truth_hype_filter — the truth/hype LEARNING antibody."""
from __future__ import annotations

import statistics
import time

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


class TestPathologicalInput:
    """Cost guard against catastrophic backtracking.

    ``_IDENTIFIER`` matches dotted/underscored names, and its separator class
    ``[._]`` overlaps ``\\w`` on the underscore, so a run of underscores can be
    split many ways. CodeQL flags that ambiguity as py/redos. Measured, it is
    not reachable: the pattern ends in ``\\b``, which succeeds after any word
    character, so the engine finds a match on the first pass instead of
    exhausting the splits. The worst input found runs in well under a
    millisecond, and ``evaluate`` truncates its input before any regex sees it.

    These tests pin down the two ways that could regress:
      * someone removes the truncation, letting an unbounded string through;
      * someone "fixes" the pattern and introduces real backtracking. A
        rewrite using ``[^\\W_]*(?:[._]+[^\\W_]+)+[._]*`` was measured at
        180 ms on ``"_." * 2048`` — 800x slower than the pattern it replaced,
        because the adjacent ``[._]+``/``[._]*`` are themselves ambiguous.

    The budget is deliberately loose: catastrophic backtracking costs seconds
    or minutes, never a second, so this cannot flake on a slow runner.
    """

    BUDGET_SECONDS = 2.0

    # A pathological string costs no more than this multiple of ordinary prose
    # of the same length. Measured: 1.7x as shipped, 121x with the rewrite
    # above, so the threshold sits well clear of both. Being a ratio, it does
    # not care how fast the runner is.
    MAX_COST_RATIO = 20.0

    def _elapsed(self, text: str) -> float:
        start = time.perf_counter()
        evaluate(text)
        return time.perf_counter() - start

    def _median_cost(self, text: str, reps: int = 7) -> float:
        return statistics.median(self._elapsed(text) for _ in range(reps))

    def test_separator_runs_stay_cheap(self):
        # Each payload is far longer than the internal scan limit, so this
        # also proves the truncation in evaluate() is still in place.
        for payload in ("_." * 100_000, "a" + "_a" * 100_000, "_" * 200_000):
            elapsed = self._elapsed(payload)
            assert elapsed < self.BUDGET_SECONDS, (
                f"{payload[:8]!r}... took {elapsed:.3f}s"
            )

    def test_pathological_input_costs_about_the_same_as_prose(self):
        # Catches a rewrite that backtracks but still hides under the
        # truncation limit — too fast for a wall-clock budget to notice.
        length = 4096
        pathological = "_." * (length // 2 + 10)
        prose = ("the config parser reads a value and returns it. " * 128)[:length]
        ratio = self._median_cost(pathological) / self._median_cost(prose)
        assert ratio < self.MAX_COST_RATIO, (
            f"separator run cost {ratio:.1f}x ordinary prose of the same length"
        )

    def test_long_input_is_not_slower_than_short_one(self):
        # Truncation makes cost flat, not linear, in the length of the input.
        short = self._elapsed("a" + "_a" * 2_000)
        long = self._elapsed("a" + "_a" * 200_000)
        assert long < self.BUDGET_SECONDS
        assert short < self.BUDGET_SECONDS

    def test_identifiers_still_detected_including_cyrillic(self):
        # The repository is Russian-language; any rewrite of the identifier
        # pattern must keep Cyrillic identifiers as substance signals.
        for text in ("Смотри трасса_кода в модуле.", "See os.path.join here."):
            assert evaluate(text).signals.has_identifier is True
