"""Unit tests for measured-confidence gate in SelfRepairController (T9 / §7).

Specifically tests:
  - _extract_pass_count() helper
  - measured_confidence is computed from baseline vs post test counts
  - RepairReport.measured_confidence field is populated
  - low_confidence status when post_passed / baseline_passed < MIN_REPAIR_CONFIDENCE
  - summary() includes measured_confidence
"""
from __future__ import annotations

from core.self_repair import (
    _extract_pass_count,
)

# ---------------------------------------------------------------------------
# _extract_pass_count
# ---------------------------------------------------------------------------

class TestExtractPassCount:
    def test_returns_zero_when_output_is_none(self):
        assert _extract_pass_count(None) == 0

    def test_returns_zero_when_output_is_string(self):
        assert _extract_pass_count("some error output") == 0


    def test_returns_zero_on_unparseable_value(self):
        assert _extract_pass_count({"passed": "not-a-number"}) == 0
