"""Acceptance test for tools.base.normalize_slug (coding-skill demo)."""
from tools.base import normalize_slug


def test_normalize_slug_empty_returns_untitled():
    assert normalize_slug("") == "untitled"
    assert normalize_slug("   ") == "untitled"
    assert normalize_slug("---") == "untitled"


def test_normalize_slug_ascii_only():
    assert normalize_slug("API_v2") == "api-v2"
