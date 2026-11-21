"""C05 — what the retrieval's access bump actually buys, bitten at the consumer.

The turn-context read writes two values into the persistent store on every
retrieval (`core/loop_memory_read.py:152-170`): `access_count + 1` and
`last_accessed_at = now`. Both were certified as WRITES and neither as a
CONSEQUENCE — measured 2026-08-08: no test anywhere varied either value and
observed the decision that justifies writing it. That decision is
`archive_low_value_memory` (`core/memory_hygiene.py:478`), the only consumer
contract either value has; `_importance_score` is a private helper with a
single caller (line 510), an internal step of that contract rather than a
contract of its own.

Two values, two arcs, and their routes to the same decision differ:

  access_count      -> access_boost inside the score -> compared to threshold
                       GRADED: a used record scores its way out of the archive.

  last_accessed_at  -> (a) the recency penalty inside the score, and
                       (b) a guard at :513-518 that keeps a recently-used
                       record active even when its score is below the bar.
                       The two routes are REDUNDANT, measured 2026-08-08:
                       cutting either one alone leaves these tests green
                       because the other still keeps the record; only cutting
                       BOTH reddens. So this file bites the VALUE's arc — the
                       timestamp reaches the decision — and deliberately does
                       not claim to protect either route on its own. A single
                       route regressing stays invisible here; that is a
                       property of the code, recorded rather than papered over.

Each test below isolates one value: everything else about the two records is
identical, so a flipped verdict can only be that value's doing.

A note on why the second pair uses a weight-less tag AND still scores 0.5:
`MemoryRecord.importance` defaults to 0.5 and floors the tag base, so the
"recent" pole is held above the threshold by the score route rather than by
the guard. That was found by asking why a mutation stayed green instead of
adjusting the test to look convincing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_hygiene import archive_low_value_memory
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore

NOW = datetime.now(timezone.utc)
LONG_AGO = NOW - timedelta(days=60)


def _archived_ids(store: PersistentMemoryStore) -> list[str]:
    return archive_low_value_memory(store, now=NOW).archived


# ── arc 1: access_count -> archival decision (through the score) ──────────────

def _idle_untagged(access_count: int) -> MemoryRecord:
    """Old, never-accessed-by-date, untagged: base 0.3, full recency penalty.

    With count 0 the score lands at 0.0 (below the 0.25 threshold); the access
    boost is the only thing that can lift it back over.
    """
    return MemoryRecord(
        type="semantic",
        content="an ordinary fact nobody protected",
        owner="user",
        created_at=LONG_AGO,
        access_count=access_count,
        last_accessed_at=None,
    )


def test_a_never_retrieved_record_is_archived(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    rec = _idle_untagged(access_count=0)
    store.save(rec)

    assert _archived_ids(store) == [rec.id], (
        "precondition of the whole arc: with no accesses this record IS "
        "archivable, so the next test's difference can only be the count"
    )


def test_the_access_count_written_by_retrieval_saves_the_same_record(
    tmp_path: Path,
) -> None:
    """The consequence the bump exists for — the half never bitten before."""
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    rec = _idle_untagged(access_count=20)
    store.save(rec)

    assert _archived_ids(store) == [], (
        "a record the loop had retrieved 20 times was archived anyway — the "
        "access bump buys nothing, and writing it is pure cost"
    )


# ── arc 2: last_accessed_at -> archival decision (the independent guard) ──────

def _idle_unweighted_tag(last_accessed_at: datetime | None) -> MemoryRecord:
    """Old record whose only moving part is the access timestamp.

    The tag carries no weight, but `importance` defaults to 0.5 and floors the
    base, so the pair separates on the timestamp alone: 60 days idle scores
    0.2 (archived), accessed now scores 0.5 (kept).
    """
    return MemoryRecord(
        type="semantic",
        content="a fact with a tag nobody weighted",
        owner="user",
        tags=["misc-unweighted"],
        created_at=LONG_AGO,
        access_count=0,
        last_accessed_at=last_accessed_at,
    )


def test_a_long_unused_record_is_archived(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    rec = _idle_unweighted_tag(last_accessed_at=LONG_AGO)
    store.save(rec)

    assert _archived_ids(store) == [rec.id], (
        "precondition: last used 60 days ago, so it is archivable and the "
        "next test's difference can only be the access timestamp"
    )


def test_a_recent_access_timestamp_keeps_a_record_active(tmp_path: Path) -> None:
    """The timestamp's arc: same record, only `last_accessed_at` moves.

    Reddens when BOTH of the value's routes are cut (M40). If a record the
    agent read this minute is filed away as unused, the timestamp the
    retrieval writes has stopped buying anything.
    """
    store = PersistentMemoryStore(tmp_path / "m.jsonl")
    rec = _idle_unweighted_tag(last_accessed_at=NOW)
    store.save(rec)

    assert _archived_ids(store) == [], (
        "a record accessed just now was archived: the recency guard no longer "
        "reads last_accessed_at, so the timestamp the retrieval writes is dead"
    )
