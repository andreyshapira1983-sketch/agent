"""Memory Hygiene — backup cleanup, dedup, expiry, summarisation.

Every function in `core/hygiene.py` is exercised in isolation here. The
loop-level integration (audit events, CLI surface) lives in
`tests/test_hygiene_integration.py`.

Tests pin three invariants for every cleanup policy:
  (a) the safe defaults never delete the only surviving copy of anything
  (b) `dry_run=True` returns the same report but writes nothing
  (c) every removed item shows up in the report's audit list
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.hygiene import (
    DEFAULT_DEDUP_THRESHOLD,
    DEFAULT_KEEP_LAST,
    DEFAULT_MAX_AGE_DAYS,
    SUMMARY_TAG,
    BackupCleanupReport,
    DedupReport,
    ExpiryReport,
    SummaryReport,
    cleanup_backups,
    deduplicate_memory,
    expire_memory,
    find_duplicate,
    summarise_memory,
    _similarity,
)
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


# ===========================================================
# 1. Backup cleanup
# ===========================================================

def _make_backup(workspace: Path, target: str, ts: str, content: str = "x") -> Path:
    """Create a `<target>.bak.<ts>` file inside workspace and return its path."""
    path = workspace / f"{target}.bak.{ts}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _ts(days_ago: int) -> str:
    """Convert a days-ago offset to the strftime format file_write uses."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y%m%dT%H%M%SZ")


class TestBackupCleanupHappyPath:
    def test_empty_workspace_returns_empty_report(self, workspace: Path):
        report = cleanup_backups(workspace)
        assert isinstance(report, BackupCleanupReport)
        assert report.scanned == 0
        assert report.deleted == []
        assert report.kept == []
        assert report.dry_run is False

    def test_backup_inside_keep_last_is_kept(self, workspace: Path):
        # Only 2 backups — both inside `keep_last=3` default; both stay.
        _make_backup(workspace, "doc.txt", _ts(0))
        _make_backup(workspace, "doc.txt", _ts(30))  # very old
        report = cleanup_backups(workspace)
        assert report.scanned == 2
        assert report.deleted == []
        assert len(report.kept) == 2

    def test_old_backups_above_keep_last_are_deleted(self, workspace: Path):
        # 5 backups for one file: 3 recent (kept by floor), 2 old + over
        # keep_last (deleted).
        _make_backup(workspace, "doc.txt", _ts(0))      # newest
        _make_backup(workspace, "doc.txt", _ts(1))
        _make_backup(workspace, "doc.txt", _ts(2))
        old_a = _make_backup(workspace, "doc.txt", _ts(30))  # old
        old_b = _make_backup(workspace, "doc.txt", _ts(60))  # old

        report = cleanup_backups(workspace, keep_last=3, max_age_days=14)
        assert report.scanned == 5
        assert sorted(report.deleted) == sorted(
            [old_a.name, old_b.name]
        )
        # The kept list contains all 3 recent ones.
        assert len(report.kept) == 3
        # The actual files were removed.
        assert not old_a.exists()
        assert not old_b.exists()

    def test_keep_last_floor_protects_solo_backup(self, workspace: Path):
        # One backup, 100 days old. Must NOT be deleted (sole survivor).
        solo = _make_backup(workspace, "doc.txt", _ts(100))
        report = cleanup_backups(workspace, keep_last=3, max_age_days=14)
        assert report.deleted == []
        assert solo.exists()

    def test_keep_last_zero_still_protects_newer_than_cutoff(
        self, workspace: Path
    ):
        # keep_last=0 means the floor is disabled. Newer-than-cutoff
        # backups still must NOT be deleted (the age rule protects them).
        recent = _make_backup(workspace, "doc.txt", _ts(0))
        report = cleanup_backups(workspace, keep_last=0, max_age_days=14)
        assert report.deleted == []
        assert recent.exists()

    def test_multiple_targets_grouped_independently(self, workspace: Path):
        # Two distinct targets; each gets its own keep_last floor.
        a_recent = _make_backup(workspace, "a.txt", _ts(0))
        a_old = _make_backup(workspace, "a.txt", _ts(30))
        b_recent = _make_backup(workspace, "b.txt", _ts(0))

        report = cleanup_backups(workspace, keep_last=1, max_age_days=14)
        # `a.txt` has the recent one + 1 old: floor protects the recent;
        # the old is above floor + older than cutoff -> deleted.
        # `b.txt` has only the recent one -> kept by floor.
        assert sorted(report.deleted) == [a_old.name]
        assert a_recent.exists()
        assert b_recent.exists()

    def test_dry_run_reports_but_does_not_delete(self, workspace: Path):
        _make_backup(workspace, "doc.txt", _ts(0))
        _make_backup(workspace, "doc.txt", _ts(1))
        _make_backup(workspace, "doc.txt", _ts(2))
        old = _make_backup(workspace, "doc.txt", _ts(30))

        report = cleanup_backups(
            workspace, keep_last=3, max_age_days=14, dry_run=True
        )
        assert report.dry_run is True
        assert report.deleted == [old.name]
        # File NOT actually removed.
        assert old.exists()


class TestBackupCleanupSafety:
    def test_ignores_non_backup_files(self, workspace: Path):
        # A regular file with similar-but-wrong name must NOT be touched.
        innocent = workspace / "doc.txt"
        innocent.write_text("the real file", encoding="utf-8")
        also = workspace / "doc.txt.bakup_old"
        also.write_text("not a backup", encoding="utf-8")
        report = cleanup_backups(workspace)
        assert report.scanned == 0
        assert innocent.exists()
        assert also.exists()

    def test_unparseable_timestamp_is_skipped(self, workspace: Path):
        weird = workspace / "doc.txt.bak.NOTATIMESTAMP"
        weird.write_text("garbage", encoding="utf-8")
        report = cleanup_backups(workspace)
        # Pattern requires 8 digits + 'T' + 6 digits + 'Z'; this doesn't match.
        assert report.scanned == 0
        assert weird.exists()

    def test_negative_args_rejected(self, workspace: Path):
        with pytest.raises(ValueError, match="keep_last"):
            cleanup_backups(workspace, keep_last=-1)
        with pytest.raises(ValueError, match="max_age_days"):
            cleanup_backups(workspace, max_age_days=-1)

    def test_non_existent_workspace_returns_empty_report(self, tmp_path: Path):
        """Calling cleanup on a folder that has not been created yet must
        produce an empty report, not raise."""
        ghost = tmp_path / "does_not_exist"
        report = cleanup_backups(ghost)
        assert report.scanned == 0
        assert report.deleted == []
        assert report.kept == []

    def test_unlink_oserror_keeps_file_in_audit(self, workspace: Path, monkeypatch):
        """If `Path.unlink` raises OSError (file locked, ACL etc.), the
        file must appear in `kept` and never in `deleted` so the audit
        log doesn't lie."""
        # Create one old backup that WOULD be deletable.
        old = workspace / "doc.txt.bak.20260510T000000Z"
        old.write_text("x", encoding="utf-8")
        # Two recent backups to push it over keep_last=1 threshold.
        (workspace / "doc.txt.bak.20260524T000000Z").write_text("x", encoding="utf-8")

        # Make unlink raise on this specific call.
        real_unlink = Path.unlink

        def maybe_explode(self, *a, **k):
            if self.name == old.name:
                raise OSError("simulated lock")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", maybe_explode)

        report = cleanup_backups(workspace, keep_last=1, max_age_days=14)
        # File still on disk (unlink raised).
        assert old.exists()
        # Audit truthful: NOT in deleted; IN kept.
        assert old.name not in report.deleted
        assert old.name in report.kept


# ===========================================================
# 2. Similarity + write-time dedup primitive
# ===========================================================

class TestSimilarity:
    def test_identical_strings_score_one(self):
        assert _similarity("hello world", "hello world") == 1.0

    def test_case_and_whitespace_insensitive(self):
        assert _similarity("Hello   WORLD", "hello world") == 1.0

    def test_completely_different_scores_zero_or_near(self):
        assert _similarity("apple pie", "quantum chromodynamics") <= 0.05

    def test_containment_boost(self):
        # The shorter is a prefix of the longer; Jaccard alone would be
        # mid-range, but the containment boost pushes the score up.
        short = "I prefer concise answers"
        long = "I prefer concise answers in Russian and JSON output"
        assert _similarity(short, long) >= 0.4

    def test_empty_inputs_zero(self):
        assert _similarity("", "anything") == 0.0
        assert _similarity("anything", "") == 0.0


class TestFindDuplicate:
    def _rec(self, text: str) -> MemoryRecord:
        return MemoryRecord(content=text, tags=["fact"], owner="user")

    def test_empty_inputs_return_none(self):
        assert find_duplicate("", [self._rec("x")]) is None
        assert find_duplicate("x", []) is None

    def test_below_threshold_returns_none(self):
        existing = [self._rec("apple pie")]
        assert find_duplicate("quantum mechanics", existing) is None

    def test_above_threshold_returns_match_and_score(self):
        target = self._rec("I prefer concise Russian answers")
        match = find_duplicate(
            "I prefer concise russian answers", [target]
        )
        assert match is not None
        rec, score = match
        assert rec.id == target.id
        assert score >= DEFAULT_DEDUP_THRESHOLD

    def test_returns_highest_score_winner(self):
        a = self._rec("the color of the sky is blue")
        b = self._rec("the color of the sky is blue today")  # closer
        c = self._rec("apple pie")
        match = find_duplicate("the color of the sky is blue today", [a, b, c])
        assert match is not None
        assert match[0].id == b.id


# ===========================================================
# 3. Post-hoc dedup
# ===========================================================

class _InMemoryStore:
    """Tiny `_StoreProto` shim so dedup / expiry tests don't touch disk."""

    def __init__(self, records: list[MemoryRecord] | None = None):
        self.records = list(records or [])
        self.rewrites = 0

    def load(self) -> list[MemoryRecord]:
        return list(self.records)

    def _load_raw(self) -> list[MemoryRecord]:
        return list(self.records)

    def _rewrite(self, records: list[MemoryRecord]) -> None:
        self.rewrites += 1
        self.records = list(records)


def _rec(text: str, created_at: datetime | None = None, tags=None) -> MemoryRecord:
    kwargs = dict(content=text, tags=tags or ["fact"], owner="user")
    if created_at is not None:
        kwargs["created_at"] = created_at
    return MemoryRecord(**kwargs)


class TestDedup:
    def test_empty_store_returns_empty_report(self):
        store = _InMemoryStore()
        report = deduplicate_memory(store)
        assert isinstance(report, DedupReport)
        assert report.scanned == 0
        assert report.deleted == []
        assert report.groups == []
        assert store.rewrites == 0

    def test_single_record_no_op(self):
        store = _InMemoryStore([_rec("hello")])
        report = deduplicate_memory(store)
        assert report.scanned == 1
        assert report.deleted == []
        assert store.rewrites == 0

    def test_unique_records_kept(self):
        store = _InMemoryStore([
            _rec("apple pie"),
            _rec("french onion soup"),
            _rec("quantum physics"),
        ])
        report = deduplicate_memory(store)
        assert report.deleted == []
        # No rewrite needed.
        assert store.rewrites == 0

    def test_exact_duplicate_collapsed_oldest_canonical(self):
        old = _rec("I prefer concise answers", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new = _rec("I prefer concise answers", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        store = _InMemoryStore([new, old])  # reversed order on purpose
        report = deduplicate_memory(store)

        # Oldest is canonical; newer is deleted.
        assert report.scanned == 2
        assert report.deleted == [new.id]
        assert len(report.groups) == 1
        assert report.groups[0].canonical_id == old.id
        assert new.id in report.groups[0].duplicate_ids

        # Store rewritten with only the oldest survivor.
        assert store.rewrites == 1
        assert [r.id for r in store.records] == [old.id]

    def test_near_duplicate_collapsed(self):
        canon = _rec(
            "I prefer concise Russian answers",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        dup = _rec(
            "I prefer concise russian answers",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store = _InMemoryStore([canon, dup])
        report = deduplicate_memory(store)
        assert report.deleted == [dup.id]
        assert report.groups[0].canonical_id == canon.id

    def test_dry_run_does_not_rewrite(self):
        old = _rec("hello world", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new = _rec("hello world", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([old, new])
        report = deduplicate_memory(store, dry_run=True)
        assert report.deleted == [new.id]
        assert store.rewrites == 0
        assert len(store.records) == 2

    def test_idempotent(self):
        """A second dedup pass finds zero new groups."""
        a = _rec("the same thing", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("the same thing", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        c = _rec("the same thing", created_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b, c])

        first = deduplicate_memory(store)
        second = deduplicate_memory(store)
        assert len(first.deleted) == 2
        assert second.deleted == []
        assert second.scanned == 1

    def test_threshold_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            deduplicate_memory(_InMemoryStore(), threshold=0.0)
        with pytest.raises(ValueError):
            deduplicate_memory(_InMemoryStore(), threshold=1.5)


# ===========================================================
# 4. TTL / expiration
# ===========================================================

class TestExpire:
    def test_empty_store_no_op(self):
        store = _InMemoryStore()
        report = expire_memory(store)
        assert isinstance(report, ExpiryReport)
        assert report.expired == []
        assert store.rewrites == 0

    def test_records_without_ttl_never_expire(self):
        r = MemoryRecord(content="forever", tags=["fact"], owner="user", ttl_seconds=None)
        store = _InMemoryStore([r])
        report = expire_memory(store, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        assert report.expired == []
        assert store.rewrites == 0

    def test_record_within_ttl_kept(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        r = MemoryRecord(
            content="recent",
            tags=["fact"],
            owner="user",
            ttl_seconds=86400,
            created_at=now - timedelta(hours=1),  # 1 hour old; TTL=1 day
        )
        store = _InMemoryStore([r])
        report = expire_memory(store, now=now)
        assert report.expired == []

    def test_expired_record_removed_with_audit(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        fresh = MemoryRecord(content="fresh", tags=["fact"], owner="user", ttl_seconds=None)
        stale = MemoryRecord(
            content="stale",
            tags=["fact"],
            owner="user",
            ttl_seconds=60,
            created_at=now - timedelta(hours=1),  # 1 hour old; TTL=60 s
        )
        store = _InMemoryStore([fresh, stale])
        report = expire_memory(store, now=now)
        assert report.expired == [stale.id]
        assert store.rewrites == 1
        # The fresh one is still there.
        assert [r.id for r in store.records] == [fresh.id]

    def test_zero_ttl_is_treated_as_no_ttl(self):
        """ttl_seconds=0 means "no expiry policy", not "expire immediately"."""
        r = MemoryRecord(content="x", tags=["fact"], owner="user", ttl_seconds=0)
        store = _InMemoryStore([r])
        report = expire_memory(store, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        assert report.expired == []

    def test_dry_run_keeps_disk(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        stale = MemoryRecord(
            content="stale",
            tags=["fact"],
            owner="user",
            ttl_seconds=60,
            created_at=now - timedelta(hours=1),
        )
        store = _InMemoryStore([stale])
        report = expire_memory(store, now=now, dry_run=True)
        assert report.expired == [stale.id]
        assert store.rewrites == 0


# ===========================================================
# 5. Summarisation
# ===========================================================

class _FakeSummariserLLM:
    """Captures call args + returns a canned summary."""

    def __init__(self, canned: str = "Merged summary."):
        self.canned = canned
        self.calls: list[tuple[str, str]] = []
        self.provider = "fake"
        self.model = "fake-1"

    def complete(self, system: str, user: str, max_tokens=2048, temperature=0.7) -> str:
        self.calls.append((system, user))
        return self.canned


class _RaisingLLM:
    provider = "fake"
    model = "fake-1"

    def complete(self, system, user, max_tokens=2048, temperature=0.7):
        raise RuntimeError("simulated LLM failure")


class TestSummarise:
    def test_no_records_skipped(self):
        store = _InMemoryStore()
        llm = _FakeSummariserLLM()
        rep = summarise_memory(store, llm, tag="project")
        assert isinstance(rep, SummaryReport)
        assert rep.skipped_reason == "no records"
        assert rep.new_record_id is None
        assert llm.calls == []
        assert store.rewrites == 0

    def test_single_record_skipped(self):
        store = _InMemoryStore([_rec("alone", tags=["project"])])
        llm = _FakeSummariserLLM()
        rep = summarise_memory(store, llm, tag="project")
        assert rep.skipped_reason == "single record (nothing to merge)"
        assert llm.calls == []
        assert store.rewrites == 0

    def test_two_records_merged_into_one(self):
        a = _rec("fact A about project", tags=["project"], created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("fact B about project", tags=["project"], created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b])
        llm = _FakeSummariserLLM(canned="A; B.")

        rep = summarise_memory(store, llm, tag="project")
        assert rep.scanned == 2
        assert sorted(rep.summarised_ids) == sorted([a.id, b.id])
        assert rep.new_record_id is not None
        assert store.rewrites == 1

        # Originals are gone; one new record is present.
        ids_after = {r.id for r in store.records}
        assert a.id not in ids_after
        assert b.id not in ids_after
        new = next(r for r in store.records if r.id == rep.new_record_id)
        assert new.content == "A; B."
        # Summary records carry BOTH the source tag and the SUMMARY_TAG.
        assert "project" in new.tags
        assert SUMMARY_TAG in new.tags

    def test_already_summarised_records_skipped(self):
        # Records tagged BOTH "project" AND SUMMARY_TAG are excluded.
        marked = _rec(
            "already merged",
            tags=["project", SUMMARY_TAG],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        fresh = _rec(
            "new fact",
            tags=["project"],
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store = _InMemoryStore([marked, fresh])
        llm = _FakeSummariserLLM()
        rep = summarise_memory(store, llm, tag="project")
        # Only `fresh` was eligible -> single record -> skipped.
        assert rep.scanned == 1
        assert rep.skipped_reason is not None
        assert "single record" in rep.skipped_reason

    def test_summary_tag_itself_is_refused(self):
        store = _InMemoryStore([
            _rec("x", tags=[SUMMARY_TAG]),
            _rec("y", tags=[SUMMARY_TAG]),
        ])
        rep = summarise_memory(store, _FakeSummariserLLM(), tag=SUMMARY_TAG)
        assert rep.skipped_reason is not None
        assert SUMMARY_TAG in rep.skipped_reason

    def test_llm_failure_leaves_store_untouched(self):
        a = _rec("a", tags=["project"], created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("b", tags=["project"], created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b])
        rep = summarise_memory(store, _RaisingLLM(), tag="project")
        assert rep.skipped_reason is not None
        assert "llm_error" in rep.skipped_reason
        assert store.rewrites == 0
        # Records untouched.
        assert {r.id for r in store.records} == {a.id, b.id}

    def test_empty_llm_output_is_a_skip_not_a_save(self):
        a = _rec("a", tags=["project"], created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("b", tags=["project"], created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b])
        rep = summarise_memory(store, _FakeSummariserLLM(canned="   "), tag="project")
        assert rep.skipped_reason == "llm returned empty summary"
        assert store.rewrites == 0

    def test_dry_run_does_not_rewrite(self):
        a = _rec("a", tags=["project"], created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("b", tags=["project"], created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b])
        llm = _FakeSummariserLLM("merged")
        rep = summarise_memory(store, llm, tag="project", dry_run=True)
        # LLM was called (we still want a real plan), but disk is untouched.
        assert llm.calls
        assert rep.summarised_ids
        assert rep.new_record_id is None
        assert store.rewrites == 0

    def test_max_records_caps_input(self):
        # 5 candidates, max_records=3 -> only oldest 3 are merged, the
        # other 2 stay on disk.
        records = [
            _rec(
                f"fact-{i}",
                tags=["project"],
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            )
            for i in range(5)
        ]
        store = _InMemoryStore(records)
        rep = summarise_memory(store, _FakeSummariserLLM("merged"), tag="project", max_records=3)
        assert len(rep.summarised_ids) == 3
        # The two newest were not merged: they survive together with the
        # new summary record.
        remaining_originals = [r.id for r in store.records if r.id != rep.new_record_id]
        assert len(remaining_originals) == 2

    @pytest.mark.parametrize("bad_tag", ["", "   "])
    def test_empty_tag_rejected(self, bad_tag):
        with pytest.raises(ValueError, match="non-empty"):
            summarise_memory(_InMemoryStore(), _FakeSummariserLLM(), tag=bad_tag)

    def test_max_records_below_two_rejected(self):
        with pytest.raises(ValueError, match="max_records"):
            summarise_memory(_InMemoryStore(), _FakeSummariserLLM(), tag="project", max_records=1)

    def test_oversized_llm_output_truncated_to_cap(self):
        """If the LLM produces more text than max(800, combined_chars),
        the summary is truncated so the merged record is never larger
        than the originals it replaces."""
        a = _rec("short", tags=["project"], created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = _rec("short", tags=["project"], created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        store = _InMemoryStore([a, b])
        huge = "x" * 5000  # well above the 800-char floor
        llm = _FakeSummariserLLM(canned=huge)
        rep = summarise_memory(store, llm, tag="project")
        assert rep.new_record_id is not None
        new = next(r for r in store.records if r.id == rep.new_record_id)
        # Capped to the 800-char floor (combined_chars=10, so 800 wins).
        assert len(new.content) <= 801  # 800 + ellipsis
