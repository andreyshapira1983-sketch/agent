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

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.backup_cleanup import cleanup_backups
from core.memory_hygiene import (
    SUMMARY_TAG,
    _similarity,
    deduplicate_memory,
    summarise_memory,
)
from core.models import MemoryRecord

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
    def test_keep_last_zero_still_protects_newer_than_cutoff(
        self, workspace: Path
    ):
        # keep_last=0 means the floor is disabled. Newer-than-cutoff
        # backups still must NOT be deleted (the age rule protects them).
        recent = _make_backup(workspace, "doc.txt", _ts(0))
        report = cleanup_backups(workspace, keep_last=0, max_age_days=14)
        assert report.deleted == []
        assert recent.exists()


class TestBackupCleanupSafety:
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
    def test_empty_inputs_zero(self):
        assert _similarity("", "anything") == 0.0
        assert _similarity("anything", "") == 0.0


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

    def drop_ids(self, ids, *, add=()) -> int:
        self.rewrites += 1
        before = len(self.records)
        self.records = [r for r in self.records if r.id not in set(ids)]
        dropped = before - len(self.records)
        self.records += list(add)
        return dropped


def _rec(text: str, created_at: datetime | None = None, tags=None) -> MemoryRecord:
    kwargs = {"content": text, "tags": tags or ["fact"], "owner": "user"}
    if created_at is not None:
        kwargs["created_at"] = created_at
    return MemoryRecord(**kwargs)


class TestDedup:
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


    def test_threshold_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            deduplicate_memory(_InMemoryStore(), threshold=0.0)
        with pytest.raises(ValueError):
            deduplicate_memory(_InMemoryStore(), threshold=1.5)


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
    def test_single_record_skipped(self):
        store = _InMemoryStore([_rec("alone", tags=["project"])])
        llm = _FakeSummariserLLM()
        rep = summarise_memory(store, llm, tag="project")
        assert rep.skipped_reason == "single record (nothing to merge)"
        assert llm.calls == []
        assert store.rewrites == 0


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
