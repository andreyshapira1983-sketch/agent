"""Acceptance test for tools.base.normalize_slug (coding-skill demo)."""
from tools.base import normalize_slug


def test_normalize_slug_basic_words():
    assert normalize_slug("Hello World") == "hello-world"


def test_normalize_slug_collapses_non_alnum_runs():
    assert normalize_slug("  Foo---Bar!!  ") == "foo-bar"


def test_normalize_slug_empty_returns_untitled():
    assert normalize_slug("") == "untitled"
    assert normalize_slug("   ") == "untitled"
    assert normalize_slug("---") == "untitled"


def test_normalize_slug_ascii_only():
    assert normalize_slug("API_v2") == "api-v2"
