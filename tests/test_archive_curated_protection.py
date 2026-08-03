"""Regression guards for curated-memory hygiene protection (core/hygiene.py).

Background: ``archive_low_value_memory`` scored every record by tag/access/age
alone, ignoring both an explicitly-stored ``importance`` and any notion of
"curated" content. A hand-authored lesson (tags ``lesson``/``regression-guard``,
``importance=0.9``) that had gone unused for a while scored BELOW the archive
threshold and would have been silently moved out of active memory by
``:hygiene archive`` — proven below by reverting the fix (test F).

Test F FAILS on the pre-fix code (proven by temporarily reverting).
Tests E/G are guards on behavior that must hold: archived records are excluded
from retrieval, and — critically — the curated-tag protection is *targeted*,
not a blanket disable of archiving (an ordinary low-value record must still be
archived, or the hygiene tool would have silently stopped doing its job).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.hygiene import archive_low_value_memory
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


# ── E. archived records never participate in active retrieval ─────────────────

def test_archived_records_excluded_from_active_store(tmp_path: Path):
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    rec = MemoryRecord(type="semantic", content="a fact about budgets", owner="user")
    store.save(rec)
    assert len(store.load()) == 1

    assert store.archive_record(rec.id) is True
    # Active load (the retrieval source) must no longer see it.
    assert store.load() == []
    # But it is preserved in the archive (reversible, not deleted).
    archived = store.load_archive()
    assert len(archived) == 1 and archived[0].id == rec.id


# ── F. curated high-value memory is not archived ──────────────────────────────

def test_curated_high_value_record_not_archived(tmp_path: Path):
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    old = datetime.now(timezone.utc) - timedelta(days=120)
    curated = MemoryRecord(
        type="semantic", content="LESSON: verify each fix with a failing test.",
        owner="user", tags=["lesson", "regression-guard"], importance=0.9,
        created_at=old,
    )
    store.save(curated)
    report = archive_low_value_memory(store, dry_run=True)
    assert curated.id not in report.archived


# ── G. an ordinary (non-curated) low-value record IS still archived ───────────

def test_ordinary_low_value_record_is_still_archived(tmp_path: Path):
    """Guards against over-correcting F into a blanket "nothing archives" bug:
    a plain, untagged, low-importance, old, never-accessed record must still
    be selected for archiving."""
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    old = datetime.now(timezone.utc) - timedelta(days=120)
    ordinary = MemoryRecord(
        type="working", content="scratch note, no longer relevant",
        owner="self", tags=[], importance=0.1,
        created_at=old,
    )
    store.save(ordinary)
    report = archive_low_value_memory(store, dry_run=True)
    assert ordinary.id in report.archived
