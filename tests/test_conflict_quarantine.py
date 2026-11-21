"""MIR-047 — a claim that becomes conflicted must stop being ordinary evidence.

`KnowledgeWritePolicy` already refuses to WRITE a conflicted claim. The gap is
the claim that turns conflicted *after* its memory record exists: the record
stays retrievable and citable as though nothing happened.

Measuring the live store showed the real cause is not "the report is not
persisted" — detection works and 24 claims are already stored `conflicted`.
It is that a memory record carries **no link back to its claim**:

    memory_tags(claim, source) -> ["fact", "knowledge", "source-backed", type]

Nothing identifies the claim, so when it later conflicts there is no way to
know which record to act on. Quarantine had no addressee.

Two small changes close it, both reusing machinery that already exists:

  1. records carry a `claim:<id>` provenance tag
  2. `conflicted` joins QUARANTINE_TAGS, so `KnowledgeUsePolicy` filters it
     with no new code at the retrieval sites

Legacy records predate the tag and cannot be linked. They are left alone
deliberately: matching them by content would be the guess MIR-049/050 exist to
forbid. Resolution stays operator-only.

Status when written: all fail.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.knowledge_pipeline import claim_provenance_tag
from core.models import MemoryRecord


def _record(*, tags: list[str]) -> MemoryRecord:
    return MemoryRecord(
        type="semantic",          # what the knowledge pipeline writes
        content="the deploy window is Friday noon",
        tags=tags, owner="self", source="agent-auto",
        created_at=datetime.now(timezone.utc),
    )


# ==========================================================================
# 3. Marking targets only the linked record.
# ==========================================================================
def test_conflict_marks_only_the_linked_record() -> None:
    from core.knowledge_pipeline import quarantine_conflicted_records

    linked = _record(tags=["fact", claim_provenance_tag("claim-x")])
    other = _record(tags=["fact", claim_provenance_tag("claim-y")])
    legacy = _record(tags=["fact", "knowledge"])          # no provenance tag

    updated, report = quarantine_conflicted_records(
        [linked, other, legacy], conflicted_claim_ids={"claim-x"},
    )

    assert report["quarantined"] == 1
    assert "conflicted" in updated[0].tags
    assert "conflicted" not in updated[1].tags, "an unrelated claim is untouched"
    assert "conflicted" not in updated[2].tags, (
        "a legacy record has no claim link; guessing by content is exactly the "
        "misattribution MIR-049/050 forbid"
    )
    assert report["unlinked"] == 1, "legacy records are reported, not silently skipped"


def test_marking_is_idempotent() -> None:
    from core.knowledge_pipeline import quarantine_conflicted_records

    already = _record(tags=["fact", claim_provenance_tag("claim-x"), "conflicted"])

    updated, report = quarantine_conflicted_records(
        [already], conflicted_claim_ids={"claim-x"},
    )

    assert report["quarantined"] == 0
    assert report["already_quarantined"] == 1
    assert updated[0].tags.count("conflicted") == 1, "the tag must not accumulate"


def test_no_conflicts_changes_nothing() -> None:
    from core.knowledge_pipeline import quarantine_conflicted_records

    records = [_record(tags=["fact", claim_provenance_tag("claim-x")])]

    updated, report = quarantine_conflicted_records(records, conflicted_claim_ids=set())

    assert report["quarantined"] == 0
    assert updated[0].tags == records[0].tags
