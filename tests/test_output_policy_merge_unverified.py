"""Branch coverage for ``_merge_unverified`` markdown variants.

The helper is private but small and pure; testing it directly exercises every
header form (``Unverified:``, ``**Unverified:**``, ``# Unverified``, multi-line)
without standing up a full ranker chain.
"""
from __future__ import annotations

from core.output_policy import _merge_unverified


def test_merge_replaces_plain_nothing() -> None:
    out = _merge_unverified("Unverified: nothing\n", "fresh note")
    assert "Unverified: fresh note" in out
    assert "nothing" not in out


def test_merge_appends_to_existing_plain_unverified_line() -> None:
    out = _merge_unverified("Unverified: prior issue\n", "fresh note")
    assert "Unverified: prior issue; fresh note" in out


def test_merge_replaces_bold_nothing() -> None:
    out = _merge_unverified("**Unverified:** nothing\n", "fresh note")
    assert "**Unverified:** fresh note" in out


def test_merge_replaces_bold_no_colon_nothing() -> None:
    """Form ``**Unverified** nothing`` (no colon inside bold)."""
    out = _merge_unverified("**Unverified** nothing\n", "fresh note")
    assert "**Unverified:** fresh note" in out


def test_merge_replaces_bold_no_colon_with_colon_outside_nothing() -> None:
    out = _merge_unverified("**Unverified**: nothing\n", "fresh note")
    assert "**Unverified:** fresh note" in out


def test_merge_fills_empty_plain_label_with_bullet() -> None:
    """``Unverified:`` with empty value gets converted into a bullet list."""
    out = _merge_unverified("Unverified:\n", "fresh note")
    assert "Unverified:\n- fresh note" in out


def test_merge_appends_bullet_under_markdown_header() -> None:
    out = _merge_unverified("# Unverified\n", "fresh note")
    assert "# Unverified" in out
    assert "- fresh note" in out


def test_merge_appends_bullet_under_h2_markdown_header() -> None:
    out = _merge_unverified("## Unverified\n", "fresh note")
    assert "## Unverified" in out
    assert "- fresh note" in out


def test_merge_appends_section_when_no_label_present() -> None:
    out = _merge_unverified("Plain answer with no Unverified label.\n", "fresh note")
    assert out.endswith("Unverified: fresh note\n")
    assert "Plain answer" in out


def test_merge_idempotent_does_not_mutate_other_sections() -> None:
    answer = (
        "Conclusion: x\n"
        "Sources:\n1. web:example\n"
        "Confidence: high\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )
    out = _merge_unverified(answer, "note")
    assert "Confidence: high" in out
    assert "Safety: nothing" in out
    assert "Unverified: note" in out
