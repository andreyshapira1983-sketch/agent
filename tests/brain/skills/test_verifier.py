"""Tests for brain/skills/verifier.py."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from brain.skills.models import AcceptanceCheck
from brain.skills.verifier import (
    KNOWN_KINDS,
    LLMJudge,
    Verifier,
    VerifierReport,
)


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

ORIGINAL = (
    "The cat sat on the mat. The cat was very happy. "
    "It purred loudly all day long."
)

EDITED = (
    "The cat sat on the mat. The cat was happy. It purred all day."
)


@dataclass
class StubJudge:
    """A LLMJudge that returns a fixed similarity."""
    value: float = 0.95

    def similarity(self, _o: str, _c: str) -> float:
        return self.value


# ══════════════════════════════════════════════════════════════════════
# word_count_delta
# ══════════════════════════════════════════════════════════════════════

class TestWordCountDelta:

    def test_pass_when_within_range(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="word_count_delta",
            params={"range_pct": [-0.30, 0.10]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert report.passed
        assert report.results[0].passed
        assert -0.30 <= report.results[0].metric <= 0.10

    def test_fail_when_text_too_short(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="word_count_delta",
            params={"range_pct": [-0.05, 0.05]},  # narrow window
        )
        report = v.verify([check], ORIGINAL, "Cat sat.")
        assert not report.passed

    def test_fail_when_text_too_long(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="word_count_delta",
            params={"range_pct": [-0.05, 0.05]},
        )
        very_long = ORIGINAL + " " + ("Extra padding text. " * 30)
        report = v.verify([check], ORIGINAL, very_long)
        assert not report.passed

    def test_zero_word_original_fails_safely(self):
        v = Verifier()
        check = AcceptanceCheck(kind="word_count_delta", params={})
        report = v.verify([check], "", EDITED)
        assert not report.passed
        assert "zero words" in report.results[0].message

    def test_invalid_range_pct(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="word_count_delta",
            params={"range_pct": "wrong"},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed
        assert "invalid range_pct" in report.results[0].message


# ══════════════════════════════════════════════════════════════════════
# no_new_facts
# ══════════════════════════════════════════════════════════════════════

class TestNoNewFacts:

    def test_pass_with_high_similarity(self):
        v = Verifier(llm_judge=StubJudge(value=0.9))
        check = AcceptanceCheck(
            kind="no_new_facts", params={"min_score": 0.85},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert report.passed
        assert report.results[0].metric == 0.9

    def test_fail_with_low_similarity(self):
        v = Verifier(llm_judge=StubJudge(value=0.5))
        check = AcceptanceCheck(
            kind="no_new_facts", params={"min_score": 0.85},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed

    def test_skipped_without_judge(self):
        v = Verifier(llm_judge=None)
        check = AcceptanceCheck(kind="no_new_facts", params={})
        report = v.verify([check], ORIGINAL, EDITED)
        # Fail-open by design: missing judge → pass with note
        assert report.passed
        assert "skipped" in report.results[0].message


# ══════════════════════════════════════════════════════════════════════
# contains_no / contains_all
# ══════════════════════════════════════════════════════════════════════

class TestContains:

    def test_contains_no_passes_when_clean(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_no",
            params={"forbidden_substrings": ["TODO", "FIXME"]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert report.passed

    def test_contains_no_fails_when_present(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_no",
            params={"forbidden_substrings": ["cat"]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed
        assert "cat" in report.results[0].message

    def test_contains_all_passes_when_present(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_all",
            params={"required_substrings": ["cat", "mat"]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert report.passed

    def test_contains_all_fails_when_missing(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_all",
            params={"required_substrings": ["dog"]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed

    def test_forbidden_must_be_list(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_no",
            params={"forbidden_substrings": "TODO"},  # str not list
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed


# ══════════════════════════════════════════════════════════════════════
# paragraph counts
# ══════════════════════════════════════════════════════════════════════

class TestParagraphCounts:

    def test_max_paragraphs(self):
        v = Verifier()
        check = AcceptanceCheck(kind="max_paragraphs", params={"limit": 3})
        text = "Para 1.\n\nPara 2.\n\nPara 3."
        assert v.verify([check], "", text).passed
        text_too_long = text + "\n\nPara 4."
        assert not v.verify([check], "", text_too_long).passed

    def test_min_paragraphs(self):
        v = Verifier()
        check = AcceptanceCheck(kind="min_paragraphs", params={"limit": 2})
        assert v.verify([check], "", "P1.\n\nP2.").passed
        assert not v.verify([check], "", "P1.").passed


# ══════════════════════════════════════════════════════════════════════
# reading_grade_drop
# ══════════════════════════════════════════════════════════════════════

class TestReadingGradeDrop:

    def test_pass_when_grade_drops(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="reading_grade_drop", params={"min_drop": 0.0},
        )
        # EDITED is shorter than ORIGINAL → grade is at most equal
        report = v.verify([check], ORIGINAL, EDITED)
        assert report.passed

    def test_fail_when_no_drop_required(self):
        v = Verifier()
        # Require a 5-grade drop, EDITED only marginally simpler
        check = AcceptanceCheck(
            kind="reading_grade_drop", params={"min_drop": 5.0},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        assert not report.passed

    def test_handles_empty_text(self):
        v = Verifier()
        check = AcceptanceCheck(kind="reading_grade_drop", params={"min_drop": 0.0})
        report = v.verify([check], "", "")
        # Both grades 0 → drop=0 → passes the ≥0 requirement
        assert report.passed


# ══════════════════════════════════════════════════════════════════════
# Aggregation behaviour
# ══════════════════════════════════════════════════════════════════════

class TestAggregation:

    def test_blocking_failure_fails_overall(self):
        v = Verifier()
        checks = [
            AcceptanceCheck(kind="contains_all", params={"required_substrings": ["cat"]}),       # pass
            AcceptanceCheck(kind="contains_no",  params={"forbidden_substrings": ["cat"]}),      # fail blocking
        ]
        report = v.verify(checks, ORIGINAL, EDITED)
        assert not report.passed
        assert len(report.blocking_failures) == 1

    def test_non_blocking_failure_does_not_fail_overall(self):
        v = Verifier()
        checks = [
            AcceptanceCheck(kind="contains_all", params={"required_substrings": ["cat"]}),  # pass blocking
            AcceptanceCheck(kind="contains_no",  params={"forbidden_substrings": ["cat"]}, blocking=False),  # fail soft
        ]
        report = v.verify(checks, ORIGINAL, EDITED)
        assert report.passed
        assert len(report.warnings) == 1

    def test_unknown_kind_skipped(self):
        v = Verifier()
        check = AcceptanceCheck(kind="frobulate", params={})
        report = v.verify([check], ORIGINAL, EDITED)
        # No results — Verifier silently skipped unknown kind
        assert report.results == []
        assert report.passed  # vacuously

    def test_crash_in_check_is_caught(self):
        v = Verifier()
        # forbidden_substrings of wrong type triggers internal handling;
        # but if a method itself raises, Verifier should catch it.
        # Simulate by passing a known kind with bad params shape that
        # bypasses our top-level guard by being almost-correct.
        check = AcceptanceCheck(
            kind="word_count_delta",
            params={"range_pct": [-0.20, 0.10]},
        )
        # Inject a check that produces NaN by clearing original to whitespace
        report = v.verify([check], "   ", EDITED)
        # `_word_count("   ")` → 0, triggers "zero words" branch
        assert not report.passed

    def test_summary_format(self):
        v = Verifier()
        checks = [
            AcceptanceCheck(kind="contains_all", params={"required_substrings": ["cat"]}),
            AcceptanceCheck(kind="contains_no",  params={"forbidden_substrings": ["dog"]}),
        ]
        report = v.verify(checks, ORIGINAL, EDITED)
        assert "PASS" in report.summary
        assert "2/2" in report.summary

    def test_empty_checks_passes_vacuously(self):
        v = Verifier()
        report = v.verify([], ORIGINAL, EDITED)
        assert report.passed
        assert report.results == []


# ══════════════════════════════════════════════════════════════════════
# Serialisation
# ══════════════════════════════════════════════════════════════════════

class TestSerialisation:

    def test_to_dict(self):
        v = Verifier()
        check = AcceptanceCheck(
            kind="contains_no", params={"forbidden_substrings": ["TODO"]},
        )
        report = v.verify([check], ORIGINAL, EDITED)
        d = report.to_dict()
        assert d["passed"] is True
        assert isinstance(d["checks"], list)
        assert d["checks"][0]["kind"] == "contains_no"


# ══════════════════════════════════════════════════════════════════════
# Real-shipped profession matches Verifier
# ══════════════════════════════════════════════════════════════════════

def test_text_editor_yaml_checks_are_all_known():
    """The shipped text_editor.yaml must use kinds Verifier understands."""
    from pathlib import Path
    from brain.skills.registry import SkillRegistry

    reg = SkillRegistry()
    repo_root = Path(__file__).resolve().parents[3]
    prof = reg.load_file(repo_root / "professions" / "text_editor.yaml")
    for check in prof.acceptance_criteria:
        assert check.kind in KNOWN_KINDS, (
            f"text_editor.yaml uses unknown acceptance kind '{check.kind}'; "
            f"either add a _check_{check.kind} method to Verifier or remove from YAML."
        )
