"""Branch coverage for ``_merge_unverified`` markdown variants.

The helper is private but small and pure; testing it directly exercises every
header form (``Unverified:``, ``**Unverified:**``, ``# Unverified``, multi-line)
without standing up a full ranker chain.
"""
from __future__ import annotations

from core.output_policy import _merge_unverified


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
