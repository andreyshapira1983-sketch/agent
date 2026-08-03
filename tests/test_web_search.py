"""WebSearchTool — validate_output, argument validation, hard cap.

We do NOT exercise the live DuckDuckGo path here: the agent test suite is
hermetic and zero-network. `run()` is exercised end-to-end against a real
provider in manual smoke tests; what matters for safety is that the
`validate_output` contract is bulletproof, because the loop's
`verify_failed` ReplanTrigger and the Output Contract both depend on it.
"""
from __future__ import annotations

import pytest

from tools.web_search import MAX_RESULTS_CAP, WebSearchTool


# ============================================================
# Hard-fail cases
# ============================================================

class TestHardFails:
    def test_non_list_output_is_hard_fail(self):
        ok, issues = WebSearchTool().validate_output("not a list")
        assert ok is False
        assert any("expected list" in i for i in issues)

    def test_all_malformed_rows_is_hard_fail(self):
        ok, issues = WebSearchTool().validate_output(
            [{"junk": True}, {"junk": True}]
        )
        assert ok is False
        assert any("no well-formed results" in i for i in issues)

    def test_non_dict_row_is_flagged_but_does_not_panic(self):
        ok, issues = WebSearchTool().validate_output(["raw string"])
        # No well-formed rows → hard fail.
        assert ok is False
        assert any("result[0] is not a dict" in i for i in issues)


# ============================================================
# Empty result set — soft, not hard, fail
# ============================================================

class TestEmptyResults:
    def test_empty_list_is_OK_with_warning(self):
        """Empty result set is a legitimate "nothing found" — the LLM is
        told to acknowledge it. The loop's `verify_failed` path is NOT
        triggered, so an empty `web_search` does not by itself force a
        replan; the synthesizer can still produce an honest answer."""
        ok, issues = WebSearchTool().validate_output([])
        assert ok is True
        assert any("no results" in i for i in issues)


# ============================================================
# Mixed-quality result sets — pass with warnings
# ============================================================

class TestSoftWarnings:
    def test_well_formed_row_passes_clean(self):
        ok, issues = WebSearchTool().validate_output(
            [
                {
                    "title": "Hello",
                    "url": "https://example.com/",
                    "snippet": "world",
                    "source": "duckduckgo",
                }
            ]
        )
        assert ok is True
        assert issues == []

    def test_missing_snippet_is_warning_not_hard_fail(self):
        ok, issues = WebSearchTool().validate_output(
            [
                {
                    "title": "Hello",
                    "url": "https://example.com/",
                    "snippet": "",
                    "source": "duckduckgo",
                }
            ]
        )
        assert ok is True
        assert any("empty snippet" in i for i in issues)

    def test_missing_url_is_per_row_skip(self):
        # 2 rows: one with url, one without → first row makes it valid.
        ok, issues = WebSearchTool().validate_output(
            [
                {"title": "T1", "url": "", "snippet": "s"},  # bad
                {"title": "T2", "url": "https://x.io/", "snippet": "s"},  # good
            ]
        )
        assert ok is True
        assert any("missing url" in i for i in issues)

    def test_missing_title_skipped_but_does_not_panic(self):
        ok, issues = WebSearchTool().validate_output(
            [
                {"title": "", "url": "https://x.io/", "snippet": "s"},
                {"title": "ok", "url": "https://y.io/", "snippet": "s"},
            ]
        )
        assert ok is True
        assert any("missing title" in i for i in issues)


# ============================================================
# Result-count cap (warning, not hard fail)
# ============================================================

class TestResultCap:
    def test_more_than_cap_is_warning(self):
        # 11 well-formed rows; cap is 10. validate_output must warn but
        # not hard-fail — the cap is enforced in `run()`, not in validation.
        big = [
            {
                "title": f"t{i}",
                "url": f"https://x{i}.io/",
                "snippet": "s",
                "source": "duckduckgo",
            }
            for i in range(MAX_RESULTS_CAP + 1)
        ]
        ok, issues = WebSearchTool().validate_output(big)
        assert ok is True
        assert any(f"exceeds cap {MAX_RESULTS_CAP}" in i for i in issues)


# ============================================================
# Argument validation (`run` before any network call)
# ============================================================

class TestRunArgumentValidation:
    @pytest.mark.parametrize("bad_query", ["", "   ", None, 42, [1, 2, 3]])
    def test_empty_or_non_string_query_rejected_before_network(self, bad_query):
        # Must raise locally before importing ddgs / hitting DuckDuckGo —
        # otherwise a typo in the planner becomes a network round-trip.
        tool = WebSearchTool()
        with pytest.raises(ValueError, match="non-empty string"):
            tool.run(query=bad_query)  # type: ignore[arg-type]


# ============================================================
# Tool contract sanity
# ============================================================

class TestToolContract:
    def test_name_and_risk_match_architecture(self):
        tool = WebSearchTool()
        assert tool.name == "web_search"
        assert tool.risk == "read_only"

    def test_default_max_results_is_within_cap(self):
        tool = WebSearchTool()
        assert 1 <= tool.default_max_results <= MAX_RESULTS_CAP
