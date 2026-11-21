"""Curated online source library tests."""

from __future__ import annotations

import pytest

from core.source_library import (
    resolve_source_library,
)


def test_source_library_default_includes_open_knowledge_sources():
    entries = resolve_source_library()
    ids = {entry.id for entry in entries}

    assert "wikipedia" in ids
    assert "project_gutenberg" in ids
    assert "arxiv" in ids


def test_source_library_mixed_selection_expands_and_dedupes():
    entries = resolve_source_library("wikis,wikipedia,science")
    ids = [entry.id for entry in entries]

    assert ids.count("wikipedia") == 1
    assert "wikibooks" in ids
    assert "arxiv" in ids
    assert "pubmed" in ids


def test_source_library_rejects_unknown_selection():
    with pytest.raises(ValueError, match="unknown source library"):
        resolve_source_library("not_a_source")
