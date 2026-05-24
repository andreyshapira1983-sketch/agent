"""
brain/uncertainty.py — Uncertainty Quantification

The Brain self-calibrates its confidence — independent of the LLM.
The LLM reports a confidence value, but that is just ONE signal.
The UncertaintyEstimator combines multiple signals and tracks
historical calibration to produce a more reliable estimate.

Core principle:
    LLM confidence != Brain confidence
    Brain confidence = f(llm_confidence, context_quality, calibration_bias)

Signals used:
    1. llm_confidence      — direct from LLM output (primary signal)
    2. context_completeness — does Brain have history, facts, goals?
    3. input_clarity        — length and structure of the input
    4. calibration_bias     — historical correction (was Brain over/underconfident?)

Adaptive threshold:
    Default: 0.6
    Rises if Brain was historically overconfident (predicted high, was wrong)
    Falls if Brain was historically underconfident
    Bounded: [0.40, 0.80]

Usage:
    estimator = UncertaintyEstimator()
    result = estimator.estimate(llm_confidence=0.8, context=context_dict)
    if result.should_act:
        proceed()
    # After you know if the response was correct:
    estimator.calibrate(predicted=0.8, was_correct=True)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

DEFAULT_THRESHOLD   = 0.60   # Minimum confidence to act
MIN_THRESHOLD       = 0.40   # Floor — Brain is never too paranoid
MAX_THRESHOLD       = 0.80   # Ceiling — Brain is never too reckless
CALIBRATION_WINDOW  = 50     # How many samples to keep for calibration
MIN_SAMPLES_TO_ADAPT = 10    # Need at least this many before adapting threshold

# Signal weights — must sum to 1.0
W_LLM         = 0.55   # LLM's own reported confidence
W_CONTEXT     = 0.20   # Quality of available context
W_INPUT       = 0.10   # Clarity of the input
W_CALIBRATION = 0.15   # Historical calibration correction


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class UncertaintyResult:
    """
    What the UncertaintyEstimator returns after analysing one inference.

    calibrated_confidence: adjusted score combining all signals
    uncertainty_score: how uncertain Brain is (0.0 = certain, 1.0 = very uncertain)
    should_act: True if calibrated_confidence >= current adaptive threshold
    threshold_used: the threshold applied in this decision
    signals: breakdown dict — useful for logging and explainability
    reasoning: human-readable explanation of the decision
    """
    calibrated_confidence: float
    uncertainty_score: float
    should_act: bool
    threshold_used: float
    signals: dict[str, float]
    reasoning: str


@dataclass
class _CalibrationSample:
    predicted: float
    was_correct: bool


# ------------------------------------------------------------------
# UncertaintyEstimator
# ------------------------------------------------------------------

class UncertaintyEstimator:
    """
    Brain's internal confidence calibrator.

    Stateful — tracks calibration history across cycles.
    Thread-safe for read-heavy use (single-writer pattern via Brain).
    """

    def __init__(self) -> None:
        self._history: deque[_CalibrationSample] = deque(maxlen=CALIBRATION_WINDOW)
        self._threshold: float = DEFAULT_THRESHOLD
        logger.info("[UncertaintyEstimator] Initialized | threshold=%.2f", self._threshold)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(
        self,
        llm_confidence: float,
        context: dict[str, Any],
    ) -> UncertaintyResult:
        """
        Combine all signals into a calibrated confidence estimate.

        Args:
            llm_confidence: confidence reported by the LLM (0.0 – 1.0)
            context: the context dict built by ContextBuilder — used to
                     assess how much information Brain is working with

        Returns:
            UncertaintyResult with calibrated score and decision
        """
        llm_conf    = _clamp(llm_confidence)
        ctx_score   = self._context_score(context)
        input_score = self._input_score(context.get("input", ""))
        cal_signal  = self._calibration_signal()

        calibrated = (
            W_LLM         * llm_conf    +
            W_CONTEXT     * ctx_score   +
            W_INPUT       * input_score +
            W_CALIBRATION * cal_signal
        )
        calibrated    = _clamp(calibrated)
        uncertainty   = _clamp(1.0 - calibrated)
        should_act    = calibrated >= self._threshold

        signals = {
            "llm_confidence":    round(llm_conf,    3),
            "context_quality":   round(ctx_score,   3),
            "input_clarity":     round(input_score, 3),
            "calibration_bias":  round(cal_signal,  3),
            "calibrated":        round(calibrated,  3),
            "threshold":         round(self._threshold, 3),
        }

        reasoning = self._build_reasoning(signals, should_act)

        logger.debug(
            "[UncertaintyEstimator] llm=%.2f ctx=%.2f inp=%.2f cal=%.2f → calibrated=%.2f act=%s",
            llm_conf, ctx_score, input_score, cal_signal, calibrated, should_act,
        )

        return UncertaintyResult(
            calibrated_confidence=calibrated,
            uncertainty_score=uncertainty,
            should_act=should_act,
            threshold_used=self._threshold,
            signals=signals,
            reasoning=reasoning,
        )

    def calibrate(self, predicted: float, was_correct: bool) -> None:
        """
        Teach the estimator from a real outcome.

        Call this after Brain gets feedback (e.g. user confirms response
        was helpful, or a tool call succeeded/failed).

        Args:
            predicted: the calibrated_confidence that was used
            was_correct: True if the action was correct/helpful
        """
        self._history.append(_CalibrationSample(
            predicted=_clamp(predicted),
            was_correct=was_correct,
        ))
        self._adapt_threshold()
        logger.debug(
            "[UncertaintyEstimator] Calibrated | predicted=%.2f correct=%s threshold=%.2f",
            predicted, was_correct, self._threshold,
        )

    def threshold(self) -> float:
        """Return the current adaptive threshold."""
        return self._threshold

    def calibration_stats(self) -> dict:
        """Return calibration metrics — useful for monitoring."""
        if not self._history:
            return {"samples": 0, "accuracy": None, "avg_predicted": None, "bias": None}

        samples = list(self._history)
        accuracy = sum(1 for s in samples if s.was_correct) / len(samples)
        avg_predicted = sum(s.predicted for s in samples) / len(samples)
        # Positive bias = overconfident, negative = underconfident
        bias = avg_predicted - accuracy

        return {
            "samples":       len(samples),
            "accuracy":      round(accuracy, 3),
            "avg_predicted": round(avg_predicted, 3),
            "bias":          round(bias, 3),
            "threshold":     round(self._threshold, 3),
        }

    # ------------------------------------------------------------------
    # Private — signal extractors
    # ------------------------------------------------------------------

    def _context_score(self, context: dict[str, Any]) -> float:
        """
        How complete is Brain's context?
        More history + facts + goals = more certainty.
        """
        score = 0.0

        history = context.get("history", [])
        if history:
            # More history = more context — diminishing returns via log
            score += min(0.4, 0.1 * math.log1p(len(history)))

        facts = context.get("facts", [])
        if facts:
            score += min(0.3, 0.1 * math.log1p(len(facts)))

        goals = context.get("goals", [])
        if goals:
            score += 0.3   # Having a clear goal significantly reduces uncertainty

        return _clamp(score)

    def _input_score(self, raw_input: str) -> float:
        """
        How clear and processable is the input?
        Very short or very long inputs are harder to handle confidently.
        """
        if not raw_input or not raw_input.strip():
            return 0.0

        length = len(raw_input.strip())

        if length < 5:
            return 0.2    # Too short — ambiguous
        if length < 20:
            return 0.6    # Short but probably clear
        if length <= 300:
            return 1.0    # Sweet spot — clear and manageable
        if length <= 800:
            return 0.8    # Longer — still processable
        return 0.6        # Very long — higher risk of missing something

    def _calibration_signal(self) -> float:
        """
        Convert historical accuracy into a confidence signal.
        If Brain has been accurate → high signal.
        If Brain has been wrong a lot → lower signal.
        No history → neutral (0.7 — slightly below perfect).
        """
        if len(self._history) < MIN_SAMPLES_TO_ADAPT:
            return 0.7   # No track record yet — neutral-ish

        samples = list(self._history)
        accuracy = sum(1 for s in samples if s.was_correct) / len(samples)
        return _clamp(accuracy)

    def _adapt_threshold(self) -> None:
        """
        Adjust the threshold based on calibration history.
        Overconfident → raise threshold (be more cautious).
        Underconfident → lower threshold (trust Brain more).
        """
        if len(self._history) < MIN_SAMPLES_TO_ADAPT:
            return

        samples = list(self._history)
        accuracy = sum(1 for s in samples if s.was_correct) / len(samples)
        avg_predicted = sum(s.predicted for s in samples) / len(samples)
        bias = avg_predicted - accuracy  # Positive = overconfident

        # Correction: move threshold in direction of bias, but slowly
        adjustment = bias * 0.1
        self._threshold = _clamp(
            self._threshold + adjustment,
            lo=MIN_THRESHOLD,
            hi=MAX_THRESHOLD,
        )

    @staticmethod
    def _build_reasoning(signals: dict[str, float], should_act: bool) -> str:
        parts = []

        llm = signals["llm_confidence"]
        ctx = signals["context_quality"]
        cal = signals["calibration_bias"]
        cal_val = signals["calibrated"]
        thr = signals["threshold"]

        if llm < 0.5:
            parts.append(f"LLM reports low confidence ({llm:.0%})")
        if ctx < 0.3:
            parts.append("context is sparse (little history/facts/goals)")
        if cal < 0.5:
            parts.append("historical accuracy is below average")
        if cal_val < thr:
            parts.append(f"calibrated score {cal_val:.0%} is below threshold {thr:.0%}")
        else:
            parts.append(f"calibrated score {cal_val:.0%} meets threshold {thr:.0%}")

        decision = "proceeding" if should_act else "blocking action"
        summary = "; ".join(parts) if parts else "all signals nominal"
        return f"{decision.capitalize()} — {summary}."


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))
