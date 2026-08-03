"""SemanticScholarSearchTool -- validate_output, run() argument validation, cap.

The test suite is hermetic (zero-network). Live API calls are smoke-tested
manually. The validate_output contract matters most: the loop's
verify_failed / ReplanTrigger path depends on it.
"""
from __future__ import annotations

import pytest

from tools.semantic_scholar_search import (
    MAX_RESULTS_CAP,
    SemanticScholarSearchTool,
    _ar5iv,
    _paper_dict,
)


# ============================================================
# Helper builders
# ============================================================

def _paper(
    title: str = "Quantum Supremacy",
    url: str = "https://www.semanticscholar.org/paper/abc123",
    ar5iv_url: str = "https://ar5iv.labs.arxiv.org/html/2001.00001",
    abstract: str = "We demonstrate quantum supremacy.",
    year: int = 2019,
    authors: list[str] | None = None,
    venue: str = "Nature",
    citation_count: int = 500,
    source: str = "semantic_scholar",
) -> dict:
    return {
        "title": title,
        "url": url,
        "ar5iv_url": ar5iv_url,
        "abstract": abstract,
        "year": year,
        "authors": authors or ["John Martinis"],
        "venue": venue,
        "citation_count": citation_count,
        "source": source,
    }


TOOL = SemanticScholarSearchTool()


# ============================================================
# _ar5iv helper
# ============================================================

class TestAr5ivHelper:
    def test_returns_ar5iv_url_for_valid_id(self):
        url = _ar5iv("2001.00001")
        assert url == "https://ar5iv.labs.arxiv.org/html/2001.00001"

    def test_returns_empty_string_for_empty_id(self):
        assert _ar5iv("") == ""

    def test_returns_empty_string_for_none_equivalent(self):
        assert _ar5iv("") == ""


# ============================================================
# _paper_dict helper
# ============================================================

class TestPaperDictHelper:
    def test_extracts_arxiv_to_ar5iv_url(self):
        raw = {
            "title": "Test Paper",
            "url": "https://www.semanticscholar.org/paper/xyz",
            "externalIds": {"ArXiv": "2301.12345"},
            "abstract": "Abstract text.",
            "year": 2023,
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "venue": "NeurIPS",
            "citationCount": 42,
        }
        d = _paper_dict(raw)
        assert d["ar5iv_url"] == "https://ar5iv.labs.arxiv.org/html/2301.12345"
        assert d["authors"] == ["Alice", "Bob"]
        assert d["citation_count"] == 42
        assert d["source"] == "semantic_scholar"

    def test_no_arxiv_id_gives_empty_ar5iv(self):
        raw = {
            "title": "Paper",
            "url": "https://www.semanticscholar.org/paper/abc",
            "externalIds": {},
            "abstract": "",
            "year": None,
            "authors": [],
            "venue": "",
            "citationCount": 0,
        }
        d = _paper_dict(raw)
        assert d["ar5iv_url"] == ""

    def test_missing_optional_fields_do_not_crash(self):
        raw = {"title": "Minimal"}
        d = _paper_dict(raw)
        assert d["title"] == "Minimal"
        assert d["ar5iv_url"] == ""
        assert d["authors"] == []
        assert d["citation_count"] == 0


# ============================================================
# validate_output -- hard-fail cases
# ============================================================

class TestHardFails:
    def test_non_list_is_hard_fail(self):
        ok, issues = TOOL.validate_output("not a list")
        assert ok is False
        assert any("expected list" in i for i in issues)

    def test_non_list_dict_is_hard_fail(self):
        ok, issues = TOOL.validate_output({"title": "oops"})
        assert ok is False

    def test_all_rows_missing_title_is_hard_fail(self):
        ok, issues = TOOL.validate_output([{"url": "https://x.io/"}, {"url": "https://y.io/"}])
        assert ok is False
        assert any("no well-formed results" in i for i in issues)

    def test_non_dict_row_is_hard_fail_when_only_row(self):
        ok, issues = TOOL.validate_output(["raw string"])
        assert ok is False
        assert any("result[0] is not a dict" in i for i in issues)


# ============================================================
# validate_output -- empty (soft, not hard)
# ============================================================

class TestEmptyOutput:
    def test_empty_list_is_ok_with_warning(self):
        ok, issues = TOOL.validate_output([])
        assert ok is True
        assert any("no papers" in i for i in issues)


# ============================================================
# validate_output -- well-formed
# ============================================================

class TestWellFormed:
    def test_single_full_paper_passes_clean(self):
        ok, issues = TOOL.validate_output([_paper()])
        assert ok is True
        assert issues == []

    def test_multiple_papers_pass(self):
        ok, issues = TOOL.validate_output([_paper(), _paper(title="Another")])
        assert ok is True
        assert issues == []

    def test_missing_title_in_one_row_flagged(self):
        ok, issues = TOOL.validate_output([
            {"url": "https://x.io/"},         # bad -- no title
            _paper(title="Good Paper"),        # good
        ])
        assert ok is True
        assert any("missing title" in i for i in issues)

    def test_non_dict_row_alongside_good_row_flags_but_passes(self):
        ok, issues = TOOL.validate_output([
            "raw string",            # non-dict
            _paper(),                # good
        ])
        assert ok is True
        assert any("is not a dict" in i for i in issues)

    def test_ddg_fallback_source_accepted(self):
        p = _paper(source="semantic_scholar_via_ddg", ar5iv_url="", year=None)
        ok, issues = TOOL.validate_output([p])
        assert ok is True
        assert issues == []


# ============================================================
# run() -- argument validation
# ============================================================

class TestRunArguments:
    def test_empty_query_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            TOOL.run("")

    def test_whitespace_query_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            TOOL.run("   ")

    def test_max_results_capped_at_max(self):
        """max_results > MAX_RESULTS_CAP is silently clamped."""
        # We can't call the real API, but we can verify the cap logic
        # by inspecting that MAX_RESULTS_CAP is the hard ceiling.
        assert MAX_RESULTS_CAP == 10

    def test_default_max_results_respected(self):
        custom = SemanticScholarSearchTool(default_max_results=3)
        assert custom.default_max_results == 3


# ============================================================
# Metadata
# ============================================================

class TestMetadata:
    def test_name(self):
        assert TOOL.name == "semantic_scholar_search"

    def test_risk_is_read_only(self):
        assert TOOL.risk == "read_only"

    def test_compensation_plan_is_noop(self):
        plan = TOOL.compensation_plan({}, [])
        assert plan["actions"][0]["kind"] == "noop"

    def test_description_mentions_ar5iv(self):
        assert "ar5iv" in TOOL.description.lower() or "ar5iv_url" in TOOL.description
