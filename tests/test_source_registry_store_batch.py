"""Regression tests for the batched `SourceRegistryStore.save_registry`.

The legacy implementation called `save_source`/`save_claim` per record, and
each of those re-read the ENTIRE JSONL file to check for duplicates. Ingesting
into a large store was O(M*N) full-file reads — slow enough to look like a hang
once the catalog grew into the thousands of claims. `save_registry` now scans
the file once and appends new records in a single batch. These tests pin both
the dedup semantics and the "one scan, one append" behaviour.
"""
from __future__ import annotations

from pathlib import Path

from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore


def _registry(n_claims: int, *, source_locator: str = "doc.txt") -> SourceRegistry:
    reg = SourceRegistry()
    src = reg.register_source(type="file", title=source_locator, locator=source_locator)
    for i in range(n_claims):
        reg.register_claim(source_id=src.id, text=f"claim number {i}", locator=str(i))
    return reg


def test_save_registry_dedupes_across_calls(tmp_path: Path) -> None:
    store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")
    reg = _registry(3)

    first = store.save_registry(reg)
    second = store.save_registry(reg)

    assert first == {
        "sources_saved": 1,
        "claims_saved": 3,
        "sources_total": 1,
        "claims_total": 3,
    }
    # Nothing new the second time — pure dedup.
    assert second["sources_saved"] == 0
    assert second["claims_saved"] == 0

    loaded = store.load_registry()
    assert len(loaded.sources) == 1
    assert len(loaded.claims) == 3


def test_save_registry_dedupes_within_a_single_call(tmp_path: Path) -> None:
    """Two identical claims in one registry collapse to a single stored row."""
    store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")
    reg = SourceRegistry()
    src = reg.register_source(type="file", title="a.txt", locator="a.txt")
    # register_claim gives each ClaimRecord a distinct id, but the dedup key is
    # (source_id, locator, normalized text) — so these two are duplicates.
    reg.register_claim(source_id=src.id, text="same text", locator="L1")
    reg.register_claim(source_id=src.id, text="same   text", locator="L1")

    result = store.save_registry(reg)

    assert result["claims_saved"] == 1
    assert len(store.load_registry().claims) == 1


def test_save_registry_appends_only_new_records_incrementally(tmp_path: Path) -> None:
    store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")

    # Reuse ONE registry (stable source id) so the dedup key — which includes
    # source_id — lines up across the two saves.
    reg = SourceRegistry()
    src = reg.register_source(type="file", title="doc.txt", locator="doc.txt")
    for i in range(2):
        reg.register_claim(source_id=src.id, text=f"claim number {i}", locator=str(i))
    first = store.save_registry(reg)
    assert first["sources_saved"] == 1
    assert first["claims_saved"] == 2

    # Grow the same registry with two more claims, then re-save.
    for i in range(2, 4):
        reg.register_claim(source_id=src.id, text=f"claim number {i}", locator=str(i))
    result = store.save_registry(reg)

    # The source and the first two claims already exist; only 2 and 3 are new.
    assert result["sources_saved"] == 0
    assert result["claims_saved"] == 2
    loaded = store.load_registry()
    assert len(loaded.sources) == 1
    assert len(loaded.claims) == 4


def test_save_registry_scans_file_once_regardless_of_record_count(
    tmp_path: Path, monkeypatch
) -> None:
    """Guard against the O(M*N) regression: saving M records must NOT trigger
    one full-file read per record. We count reads of the backing file."""
    store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")
    store.save_registry(_registry(50))  # seed a non-trivial store

    reads = {"count": 0}
    original = SourceRegistryStore._iter_records_unlocked

    def _counting(self):  # type: ignore[no-untyped-def]
        reads["count"] += 1
        return original(self)

    monkeypatch.setattr(SourceRegistryStore, "_iter_records_unlocked", _counting)

    # Save 50 more claims (49 new + reuse) under a new source.
    reg2 = SourceRegistry()
    src = reg2.register_source(type="file", title="more.txt", locator="more.txt")
    for i in range(50):
        reg2.register_claim(source_id=src.id, text=f"fresh claim {i}", locator=str(i))

    store.save_registry(reg2)

    # A batched implementation scans the file a small constant number of times,
    # NOT once per incoming record. Anything close to 50+ means the quadratic
    # behaviour is back.
    assert reads["count"] <= 2, f"too many full-file scans: {reads['count']}"
