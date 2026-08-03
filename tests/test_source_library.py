"""Curated online source library tests."""

from __future__ import annotations

import pytest

from core.source_library import list_source_library, resolve_source_library, source_library_payload


def test_source_library_default_includes_open_knowledge_sources():
    entries = resolve_source_library()
    ids = {entry.id for entry in entries}

    assert "wikipedia" in ids
    assert "project_gutenberg" in ids
    assert "arxiv" in ids


def test_source_library_group_and_domain_filtering():
    entries = resolve_source_library("wikis")
    ids = [entry.id for entry in entries]

    assert ids == ["wikipedia", "wikibooks", "wikisource"]
    assert entries[0].allows_url("https://en.wikipedia.org/wiki/Agent")
    assert not entries[0].allows_url("https://example.com/wiki/Agent")


def test_source_library_all_contains_every_registered_source_once():
    all_entries = resolve_source_library("all")
    registered = list_source_library()

    assert len(all_entries) == len(registered)
    assert len({entry.id for entry in all_entries}) == len(all_entries)


def test_source_library_mixed_selection_expands_and_dedupes():
    entries = resolve_source_library("wikis,wikipedia,science")
    ids = [entry.id for entry in entries]

    assert ids.count("wikipedia") == 1
    assert "wikibooks" in ids
    assert "arxiv" in ids
    assert "pubmed" in ids


def test_source_library_payload_lists_groups_and_sources():
    payload = source_library_payload()

    assert "groups" in payload
    assert "sources" in payload
    assert "books" in payload["groups"]
    assert any(row["id"] == "open_library" for row in payload["sources"])


def test_source_library_rejects_unknown_selection():
    with pytest.raises(ValueError, match="unknown source library"):
        resolve_source_library("not_a_source")
