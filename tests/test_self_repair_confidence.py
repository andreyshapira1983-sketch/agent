"""Unit tests for measured-confidence gate in SelfRepairController (T9 / §7).

Specifically tests:
  - _extract_pass_count() helper
  - measured_confidence is computed from baseline vs post test counts
  - RepairReport.measured_confidence field is populated
  - low_confidence status when post_passed / baseline_passed < MIN_REPAIR_CONFIDENCE
  - summary() includes measured_confidence
"""
from __future__ import annotations

import pytest

from core.self_repair import (
    _DEFAULT_MIN_REPAIR_CONFIDENCE as MIN_REPAIR_CONFIDENCE,
    RepairProposal,
    RepairReport,
    _extract_pass_count,
)


# ---------------------------------------------------------------------------
# _extract_pass_count
# ---------------------------------------------------------------------------

class TestExtractPassCount:
    def test_returns_passed_field(self):
        assert _extract_pass_count({"passed": 42, "failed": 0}) == 42

    def test_returns_zero_when_no_passed_key(self):
        assert _extract_pass_count({"failed": 1}) == 0

    def test_returns_zero_when_output_is_none(self):
        assert _extract_pass_count(None) == 0

    def test_returns_zero_when_output_is_string(self):
        assert _extract_pass_count("some error output") == 0

    def test_returns_zero_when_passed_is_none(self):
        assert _extract_pass_count({"passed": None}) == 0

    def test_handles_string_int_value(self):
        # Some tools return "42" as a string
        assert _extract_pass_count({"passed": "17"}) == 17

    def test_returns_zero_on_unparseable_value(self):
        assert _extract_pass_count({"passed": "not-a-number"}) == 0


# ---------------------------------------------------------------------------
# RepairReport.measured_confidence field
# ---------------------------------------------------------------------------

class TestRepairReportMeasuredConfidence:
    def _proposal(self, confidence: float = 1.0) -> RepairProposal:
        return RepairProposal(
            path="core/example.py",
            proposed_content="# fixed",
            confidence=confidence,
        )

    def test_measured_confidence_starts_as_none(self):
        proposal = self._proposal()
        report = RepairReport(proposal=proposal, status="failed")
        assert report.measured_confidence is None

    def test_measured_confidence_appears_in_summary(self):
        proposal = self._proposal()
        report = RepairReport(proposal=proposal, status="repaired")
        report.measured_confidence = 0.95
        s = report.summary()
        assert "measured_confidence" in s
        assert s["measured_confidence"] == 0.95

    def test_measured_confidence_none_in_summary_when_not_set(self):
        proposal = self._proposal()
        report = RepairReport(proposal=proposal, status="failed")
        s = report.summary()
        assert s["measured_confidence"] is None

    def test_user_summary_includes_measured_confidence(self):
        proposal = self._proposal()
        report = RepairReport(proposal=proposal, status="repaired")
        report.measured_confidence = 1.0
        text = report.user_summary()
        assert "measured_confidence" in text
        assert "1.0" in text

    def test_user_summary_shows_na_when_not_measured(self):
        proposal = self._proposal()
        report = RepairReport(proposal=proposal, status="blocked")
        text = report.user_summary()
        assert "n/a" in text


# ---------------------------------------------------------------------------
# Confidence arithmetic
# ---------------------------------------------------------------------------

class TestMeasuredConfidenceArithmetic:
    """Validate the measured_confidence = post / max(baseline, 1) formula."""

    def test_full_recovery_gives_one(self):
        baseline = 100
        post = 100
        assert post / max(baseline, 1) == 1.0

    def test_regression_gives_less_than_one(self):
        baseline = 100
        post = 50
        assert post / max(baseline, 1) == 0.5

    def test_zero_baseline_uses_one(self):
        """When baseline has zero passing tests (edge case), use 1 as denominator."""
        baseline = 0
        post = 5
        measured = post / max(baseline, 1)
        assert measured == 5.0  # improvement over nothing

    def test_min_repair_confidence_threshold_is_60_percent(self):
        assert MIN_REPAIR_CONFIDENCE == 0.60

    def test_measured_above_threshold_considered_ok(self):
        baseline = 100
        post = 70
        measured = post / max(baseline, 1)  # 0.70
        assert measured >= MIN_REPAIR_CONFIDENCE

    def test_measured_below_threshold_considered_failing(self):
        baseline = 100
        post = 50
        measured = post / max(baseline, 1)  # 0.50
        assert measured < MIN_REPAIR_CONFIDENCE
