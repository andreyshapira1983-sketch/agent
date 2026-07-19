"""MIR-051 — restore the Beta-smoothing invariant on stored procedure confidence.

45 of 65 procedures in the live store hold `confidence=1.0`, a value
`(s+1)/(s+2)` cannot produce for any counter pair; 39 of them have a single
success where the formula gives 0.667. The code is correct — both writers
smooth — and the split is sharp at 2026-07-11, when smoothing landed and
existing rows were never migrated.

This is a DATA defect, deliberately kept out of MIR-048 (a missing code path).
Different cause, blast radius, fix, completion criteria and migration risk.

Invariant to restore:

    confidence == _smoothed_confidence(success_count, failure_count)

What the migration must NOT disturb, because a migration that quietly changes
these is worse than the defect it fixes:

    success_count / failure_count   history is not invented (that is MIR-048)
    created_at / updated_at         freshness must not be refreshed, or stale
                                    procedures start looking recently used and
                                    outlive hygiene
    id / workflow_key / provenance  identity and source episodes
    name / steps / trigger_tags     the procedure's own content

Records are written through the store's own writer, so the integrity envelope
is recomputed normally rather than hand-edited — and a record that is already
consistent must come out byte-identical, leaving checksums and audit untouched.

Every test here builds its fixture through the store, so it exercises the real
on-disk envelope shape rather than a convenient in-memory object.

Status when written: all fail — `recompute_legacy_confidence` does not exist.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.smart_memory import (
    ProceduralMemoryStore,
    ProcedureRecord,
    _smoothed_confidence,
)

_FROZEN_CREATED = "2026-06-28T10:00:00+00:00"
_FROZEN_UPDATED = "2026-06-28T10:00:00+00:00"


def _proc(
    pid: str, *, success: int, failure: int = 0, confidence: float,
    status: str = "active",
) -> ProcedureRecord:
    return ProcedureRecord(
        name=f"Workflow {pid}", workflow_key=f"tools:{pid}",
        trigger_tags=("t",), steps=("step one",),
        source_episode_ids=(f"ep-{pid}",),
        success_count=success, failure_count=failure,
        confidence=confidence, status=status,  # type: ignore[arg-type]
        id=pid, created_at=_FROZEN_CREATED, updated_at=_FROZEN_UPDATED,
    )


def _store(tmp_path: Path, procs: list[ProcedureRecord]) -> ProceduralMemoryStore:
    """Seed through the real writer, so rows carry a real integrity envelope."""
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite(procs)
    return store


def _raw_lines(store: ProceduralMemoryStore) -> list[str]:
    return store.path.read_text(encoding="utf-8").splitlines()


# ==========================================================================
# The live data shape the fix must handle.
# ==========================================================================
def test_the_defect_is_reproducible_on_the_real_envelope(tmp_path: Path) -> None:
    """Pin the exact shape measured in production, envelope included."""
    store = _store(tmp_path, [_proc("legacy", success=1, failure=0, confidence=1.0)])

    envelope = json.loads(_raw_lines(store)[0])
    assert set(envelope) == {"_integrity", "payload"}, "must exercise the real row shape"

    payload = envelope["payload"]
    assert payload["success_count"] == 1
    assert payload["failure_count"] == 0
    assert payload["confidence"] == 1.0
    assert _smoothed_confidence(1, 0) == 0.667, "what the formula says it should be"


# ==========================================================================
# Correction, per the required scenarios.
# ==========================================================================
@pytest.mark.parametrize(
    "success,failure,stored,expected",
    [
        (1, 0, 1.0, 0.667),
        (2, 0, 1.0, 0.75),
        (3, 0, 1.0, 0.8),
        (1, 1, 1.0, 0.5),
    ],
)
def test_unsmoothed_confidence_is_recomputed(
    tmp_path: Path, success: int, failure: int, stored: float, expected: float
) -> None:
    store = _store(tmp_path, [_proc("p", success=success, failure=failure, confidence=stored)])

    report = store.recompute_legacy_confidence(dry_run=False)

    assert report["corrected"] == 1
    assert store.load()[0].confidence == expected


def test_already_consistent_records_are_left_byte_identical(tmp_path: Path) -> None:
    """Rewriting a correct record would churn checksums and audit for nothing."""
    store = _store(tmp_path, [_proc("ok", success=2, failure=0, confidence=0.75)])
    before = _raw_lines(store)

    report = store.recompute_legacy_confidence(dry_run=False)

    assert report["already_consistent"] == 1
    assert report["corrected"] == 0
    assert _raw_lines(store) == before, "a consistent record must not be rewritten"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("p", success=1, failure=0, confidence=1.0)])

    first = store.recompute_legacy_confidence(dry_run=False)
    after_first = _raw_lines(store)
    second = store.recompute_legacy_confidence(dry_run=False)

    assert first["corrected"] == 1
    assert second["corrected"] == 0, "a second run must be a no-op"
    assert _raw_lines(store) == after_first


# ==========================================================================
# What must survive untouched.
# ==========================================================================
def test_counters_and_freshness_are_never_touched(tmp_path: Path) -> None:
    """The two mistakes that would make this migration worse than the defect."""
    store = _store(tmp_path, [_proc("p", success=2, failure=1, confidence=1.0)])

    store.recompute_legacy_confidence(dry_run=False)
    got = store.load()[0]

    assert (got.success_count, got.failure_count) == (2, 1), "history must not be invented"
    assert got.created_at == _FROZEN_CREATED
    assert got.updated_at == _FROZEN_UPDATED, (
        "refreshing updated_at would make stale procedures look recently used "
        "and let them outlive hygiene"
    )


def test_identity_and_content_are_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("keepme", success=1, failure=0, confidence=1.0)])
    before = store.load()[0]

    store.recompute_legacy_confidence(dry_run=False)
    after = store.load()[0]

    for field in ("id", "workflow_key", "name", "steps", "trigger_tags",
                  "source_episode_ids"):
        assert getattr(after, field) == getattr(before, field), field


def test_invalid_counters_are_skipped_not_guessed(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("bad", success=-3, failure=0, confidence=1.0)])

    report = store.recompute_legacy_confidence(dry_run=False)

    assert report["invalid"] == 1
    assert report["corrected"] == 0
    assert store.load()[0].confidence == 1.0, "a nonsensical record is reported, not repaired"


# ==========================================================================
# Shadow and bounded runs.
# ==========================================================================
def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("p", success=1, failure=0, confidence=1.0)])
    before = _raw_lines(store)

    report = store.recompute_legacy_confidence(dry_run=True)

    assert report["dry_run"] is True
    assert report["eligible"] == 1
    assert report["corrected"] == 1, "shadow still reports what it would fix"
    assert _raw_lines(store) == before, "shadow must not write"


def test_limit_bounds_the_pass(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_proc(f"p{i}", success=1, failure=0, confidence=1.0) for i in range(5)],
    )

    report = store.recompute_legacy_confidence(dry_run=False, limit=2)

    assert report["corrected"] == 2
    assert report["limit_reached"] is True
    assert sum(1 for p in store.load() if p.confidence == 1.0) == 3, "rest untouched"
    # ...and the pass is resumable: the remainder is picked up next time.
    assert store.recompute_legacy_confidence(dry_run=False)["corrected"] == 3


def test_report_carries_the_required_fields(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [
            _proc("legacy", success=1, failure=0, confidence=1.0),
            _proc("ok", success=2, failure=0, confidence=0.75),
            _proc("bad", success=-1, failure=0, confidence=1.0),
        ],
    )

    report = store.recompute_legacy_confidence(dry_run=True)

    for key in ("scanned", "eligible", "corrected", "already_consistent",
                "invalid", "skipped", "before_distribution",
                "after_distribution", "dry_run", "limit_reached"):
        assert key in report, f"missing report field: {key}"
    assert report["scanned"] == 3
    assert report["before_distribution"]["1.0"] == 2
    assert report["after_distribution"]["0.667"] == 1
