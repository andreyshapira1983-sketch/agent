"""Unit tests for measured-confidence gate in SelfRepairController (T9 / §7).

Specifically tests:
  - _extract_pass_count() helper
  - measured_confidence is computed from baseline vs post test counts
  - RepairReport.measured_confidence field is populated
  - low_confidence status when post_passed / baseline_passed < MIN_REPAIR_CONFIDENCE
  - summary() includes measured_confidence
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.self_repair import (
    _DEFAULT_MIN_REPAIR_CONFIDENCE as MIN_REPAIR_CONFIDENCE,
)
from core.self_repair import (
    RepairProposal,
    RepairReport,
    SelfRepairController,
    _extract_pass_count,
)
from tests.test_self_repair_controller_branches import (
    _BASELINE_RED,
    _FakeAgent,
    _proposal,
    _success_tool_outputs,
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

def _post(passed: int, failed: int = 0) -> dict[str, Any]:
    return {
        "timed_out": False, "exit_code": 1 if failed else 0,
        "passed": passed, "failed": failed, "errors": 0,
    }


class TestMeasuredConfidenceArithmetic:
    """Validate the measured_confidence = post / max(baseline, 1) formula."""

    @pytest.mark.parametrize(
        ("baseline_passed", "post", "measured", "status", "gated"),
        [
            pytest.param(100, _post(100), 1.0, "repaired", False,
                         id="full_recovery_gives_one"),
            pytest.param(100, _post(50, failed=50), 0.5, "rolled_back", True,
                         id="regression_gives_less_than_one"),
            pytest.param(0, _post(5), 5.0, "repaired", False,
                         id="zero_baseline_uses_one"),
            pytest.param(100, _post(70), 0.7, "repaired", False,
                         id="measured_above_threshold_considered_ok"),
            pytest.param(100, _post(50), 0.5, "rolled_back", True,
                         id="measured_below_threshold_considered_failing"),
        ],
    )
    def test_controller_run_measures_and_gates(
        self, tmp_path: Path, baseline_passed: int, post: dict[str, Any],
        measured: float, status: str, gated: bool,
    ):
        """run() reports post/max(baseline, 1) and rolls back below the threshold.

        The green-post 0.5 case is the one only this gate stops: tests that
        vanished after the patch still exit 0.
        """
        outputs = _success_tool_outputs()
        outputs["run_tests"] = {"outputs": [
            {"output": {**_BASELINE_RED, "passed": baseline_passed}},
            {"output": post},
        ]}
        agent = _FakeAgent(tool_outputs=outputs)

        report = SelfRepairController(agent, workspace_root=tmp_path).run(_proposal())

        assert report.measured_confidence == measured
        assert report.status == status
        gate = [s for s in report.steps if s.name == "measured_confidence_gate"]
        assert [s.status for s in gate] == (["blocked"] if gated else [])
        written = [p.id for p in agent.compensation_log]
        assert agent.rolled_back_plan_ids == (written if status == "rolled_back" else [])

    def test_min_repair_confidence_threshold_is_60_percent(self):
        assert MIN_REPAIR_CONFIDENCE == 0.60
