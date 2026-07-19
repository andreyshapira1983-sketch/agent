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

import pytest

from core.knowledge_pipeline import KnowledgeWritePolicy, claim_provenance_tag
from core.knowledge_use_policy import QUARANTINE_TAGS, KnowledgeUsePolicy
from core.models import MemoryRecord
from core.role_router import RoleContext
from core.source_registry import ClaimRecord, SourceRecord


def _claim(cid: str = "claim-1", *, status: str = "extracted") -> ClaimRecord:
    return ClaimRecord(
        id=cid, source_id="src-1", text="the deploy window is Friday noon",
        confidence=0.9, status=status,  # type: ignore[arg-type]
    )


def _source() -> SourceRecord:
    return SourceRecord(
        id="src-1", type="file", title="notes",  # type: ignore[arg-type]
        locator="notes.md", trust_level=0.9,
    )


def _record(*, tags: list[str]) -> MemoryRecord:
    return MemoryRecord(
        type="semantic",          # what the knowledge pipeline writes
        content="the deploy window is Friday noon",
        tags=tags, owner="self", source="agent-auto",
        created_at=datetime.now(timezone.utc),
    )


def _allowed(records: list[MemoryRecord]) -> list[MemoryRecord]:
    report = KnowledgeUsePolicy().filter(
        records,
        role_context=RoleContext(
            role="assistant", tone="neutral", output_style="prose",
            knowledge_scopes=("*",), allowed_memory_types=("semantic",),
            allowed_memory_tags=("*",),
        ),
        question="deploy window",
    )
    return list(report.allowed)


# ==========================================================================
# 1. Provenance — without it quarantine has no addressee.
# ==========================================================================
def test_memory_tags_carry_the_claim_id() -> None:
    tags = KnowledgeWritePolicy().memory_tags(_claim("claim-42"), _source())

    assert claim_provenance_tag("claim-42") in tags, (
        "a record with no link to its claim cannot be quarantined later — this "
        "is why conflict detection had nothing to act on"
    )
    # the existing tags must survive; they drive other policies
    for existing in ("fact", "knowledge", "source-backed"):
        assert existing in tags


def test_provenance_tag_is_stable_and_specific() -> None:
    assert claim_provenance_tag("a") != claim_provenance_tag("b")
    assert claim_provenance_tag("a") == claim_provenance_tag("a")


# ==========================================================================
# 2. `conflicted` must actually gate retrieval.
# ==========================================================================
def test_conflicted_is_a_quarantine_tag() -> None:
    assert "conflicted" in QUARANTINE_TAGS


def test_a_conflicted_record_is_not_retrieved() -> None:
    clean = _record(tags=["fact", "knowledge"])
    conflicted = _record(tags=["fact", "knowledge", "conflicted"])

    allowed = _allowed([clean, conflicted])

    assert clean in allowed
    assert conflicted not in allowed, (
        "a record whose claim is contradicted must stop serving as ordinary "
        "evidence until an operator resolves it"
    )


def test_operator_can_restore_by_removing_the_tag() -> None:
    """Resolution is operator-only; clearing the tag returns the record."""
    restored = _record(tags=["fact", "knowledge"])

    assert restored in _allowed([restored])


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

    by_content = {id(r): r for r in updated}
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
