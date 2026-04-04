"""Тесты для knowledge/data_lifecycle.py — DataLifecycleManager, DataAge."""

import time
import pytest
from unittest.mock import MagicMock, patch
from knowledge.data_lifecycle import DataAge, DataLifecycleManager


# ── DataAge enum ──────────────────────────────────────────────────────────────

class TestDataAge:
    def test_values(self):
        assert DataAge.FRESH.value == "fresh"
        assert DataAge.RECENT.value == "recent"
        assert DataAge.OLD.value == "old"
        assert DataAge.STALE.value == "stale"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_knowledge(items: dict | None = None):
    """Creates a mock KnowledgeSystem with long_term storage."""
    ks = MagicMock()
    store = dict(items or {})

    ks.long_term_items.return_value = store.items()
    ks.long_term_keys.return_value = store.keys()
    ks.get_long_term.side_effect = store.get
    ks.store_long_term.side_effect = lambda k, v, **kw: store.__setitem__(k, v)
    ks.delete_long_term.side_effect = lambda k: store.pop(k, None)
    return ks


# ── DataLifecycleManager ─────────────────────────────────────────────────────

class TestDataLifecycleManagerInit:
    def test_init_defaults(self):
        dlm = DataLifecycleManager()
        assert dlm.knowledge is None
        assert dlm.cognitive_core is None
        assert dlm.monitoring is None
        assert dlm._archive == {}
        assert dlm._quality_scores == {}


class TestDeduplicate:
    def test_no_knowledge(self):
        dlm = DataLifecycleManager()
        result = dlm.deduplicate()
        assert result == {"removed": 0, "duplicates": []}

    def test_no_duplicates(self):
        ks = _make_knowledge({"a": "alpha", "b": "beta"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        result = dlm.deduplicate()
        assert result["removed"] == 0

    def test_removes_duplicates(self):
        ks = _make_knowledge({"a": "same text", "b": "same text", "c": "other"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        result = dlm.deduplicate()
        assert result["removed"] == 1
        assert len(result["duplicates"]) == 1


class TestFindSimilar:
    def test_no_knowledge(self):
        dlm = DataLifecycleManager()
        assert dlm.find_similar() == []

    def test_no_cognitive_core(self):
        ks = _make_knowledge({"a": "text"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        # No cognitive_core → returns []
        assert dlm.find_similar() == []

    def test_finds_similar_keys(self):
        ks = _make_knowledge({"topic:python": "x", "topic:python:v2": "y"})
        dlm = DataLifecycleManager(knowledge_system=ks, cognitive_core=MagicMock())
        pairs = dlm.find_similar(threshold=0.3)
        # Keys share "topic" and "python" tokens
        assert len(pairs) >= 1


class TestMergeSimilar:
    def test_no_knowledge(self):
        dlm = DataLifecycleManager()
        assert dlm.merge_similar("a", "b") == "a"

    def test_merge_with_cognitive_core(self):
        ks = _make_knowledge({"a": "alpha", "b": "beta"})
        cc = MagicMock()
        cc.reasoning.return_value = "alpha + beta merged"
        dlm = DataLifecycleManager(knowledge_system=ks, cognitive_core=cc)
        result = dlm.merge_similar("a", "b", merged_key="ab")
        assert result == "ab"
        ks.store_long_term.assert_called()
        ks.delete_long_term.assert_any_call("a")
        ks.delete_long_term.assert_any_call("b")

    def test_merge_without_cognitive_core(self):
        ks = _make_knowledge({"a": "alpha", "b": "beta"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        result = dlm.merge_similar("a", "b")
        assert result == "a"


class TestArchive:
    def test_archive_no_knowledge(self):
        dlm = DataLifecycleManager()
        dlm.archive("key")  # no error

    def test_archive_key_not_found(self):
        ks = _make_knowledge({"x": "val"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm.archive("unknown")
        assert "unknown" not in dlm._archive

    def test_archive_success(self):
        ks = _make_knowledge({"mykey": "myval"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm.archive("mykey", reason="test")
        assert "mykey" in dlm._archive
        assert dlm._archive["mykey"]["value"] == "myval"
        assert dlm._archive["mykey"]["reason"] == "test"
        ks.delete_long_term.assert_called_with("mykey")

    def test_archive_persists_to_file(self, tmp_path):
        archive_file = tmp_path / "archive.json"
        ks = _make_knowledge({"k": "v"})
        dlm = DataLifecycleManager(knowledge_system=ks, archive_path=str(archive_file))
        dlm.archive("k")
        assert archive_file.exists()


class TestArchiveStale:
    def test_archives_old_entries(self):
        ks = _make_knowledge({"old_key": "old_val"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        # Simulate old timestamp (100 days ago)
        dlm._timestamps["old_key"] = time.time() - 100 * 86400
        # Quality score above mastery threshold
        dlm._quality_scores["old_key"] = 0.8
        result = dlm.archive_stale(max_age_days=90)
        assert result["archived"] == 1

    def test_skips_unmastered(self):
        ks = _make_knowledge({"key": "val"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._timestamps["key"] = time.time() - 100 * 86400
        dlm._quality_scores["key"] = 0.1  # low quality → not mastered
        result = dlm.archive_stale(max_age_days=90, require_mastery=True, min_mastery_score=0.65)
        assert result["archived"] == 0
        assert "key" in result["skipped_not_mastered"]

    def test_archives_without_mastery_check(self):
        ks = _make_knowledge({"key": "val"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._timestamps["key"] = time.time() - 100 * 86400
        result = dlm.archive_stale(max_age_days=90, require_mastery=False)
        assert result["archived"] == 1


class TestRestore:
    def test_restore_from_archive(self):
        ks = _make_knowledge({})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._archive["k"] = {"value": "restored_val", "archived_at": time.time(), "reason": "test"}
        assert dlm.restore("k") is True
        assert "k" not in dlm._archive
        ks.store_long_term.assert_called()

    def test_restore_key_not_in_archive(self):
        ks = _make_knowledge({})
        dlm = DataLifecycleManager(knowledge_system=ks)
        assert dlm.restore("unknown") is False

    def test_restore_no_knowledge(self):
        dlm = DataLifecycleManager()
        dlm._archive["k"] = {"value": "v"}
        assert dlm.restore("k") is False

    def test_get_archive(self):
        dlm = DataLifecycleManager()
        dlm._archive["a"] = {"value": "x"}
        result = dlm.get_archive()
        assert result == {"a": {"value": "x"}}


class TestQuality:
    def test_assess_quality_empty(self):
        dlm = DataLifecycleManager()
        assert dlm.assess_quality("k", "") == 0.0
        assert dlm.assess_quality("k", "None") == 0.0

    def test_assess_quality_short(self):
        dlm = DataLifecycleManager()
        score = dlm.assess_quality("k", "short text")
        assert score == 0.5

    def test_assess_quality_medium(self):
        dlm = DataLifecycleManager()
        score = dlm.assess_quality("k", "x" * 150)
        assert score == 0.7

    def test_assess_quality_long(self):
        dlm = DataLifecycleManager()
        score = dlm.assess_quality("k", "x" * 600)
        assert score == pytest.approx(0.8)

    def test_purge_low_quality_no_knowledge(self):
        dlm = DataLifecycleManager()
        assert dlm.purge_low_quality() == {"removed": 0}

    def test_purge_low_quality(self):
        ks = _make_knowledge({"good": "x" * 200, "bad": ""})
        dlm = DataLifecycleManager(knowledge_system=ks)
        result = dlm.purge_low_quality(min_score=0.3)
        assert result["removed"] >= 1
        assert "bad" in result["keys"]


class TestRunMaintenance:
    def test_full_maintenance(self):
        ks = _make_knowledge({"a": "unique", "b": "unique", "c": "other"})
        dlm = DataLifecycleManager(knowledge_system=ks)
        result = dlm.run_maintenance(archive_older_than_days=60, min_quality=0.3)
        assert "duplicates_removed" in result
        assert "stale_archived" in result
        assert "low_quality_removed" in result
        assert "archive_size" in result


class TestHelpers:
    def test_track(self):
        dlm = DataLifecycleManager()
        dlm.track("mykey")
        assert "mykey" in dlm._timestamps

    def test_hash_consistent(self):
        dlm = DataLifecycleManager()
        h1 = dlm._hash("test")
        h2 = dlm._hash("test")
        assert h1 == h2

    def test_hash_case_insensitive(self):
        dlm = DataLifecycleManager()
        assert dlm._hash("Test") == dlm._hash("test")

    def test_key_similarity_identical(self):
        dlm = DataLifecycleManager()
        assert dlm._key_similarity("a:b", "a:b") == 1.0

    def test_key_similarity_partial(self):
        dlm = DataLifecycleManager()
        s = dlm._key_similarity("a:b:c", "a:b:d")
        assert 0.0 < s < 1.0

    def test_key_similarity_empty(self):
        dlm = DataLifecycleManager()
        assert dlm._key_similarity("", "a") == 0.0

    def test_log_with_monitoring(self):
        mon = MagicMock()
        dlm = DataLifecycleManager(monitoring=mon)
        dlm._log("test message")
        mon.info.assert_called_once()

    def test_log_without_monitoring(self, capsys):
        dlm = DataLifecycleManager()
        dlm._log("test message")
        out = capsys.readouterr().out
        assert "DataLifecycle" in out

    def test_is_mastered_no_knowledge(self):
        dlm = DataLifecycleManager()
        assert dlm._is_mastered("k", 0.5) is False

    def test_is_mastered_no_value(self):
        ks = _make_knowledge({})
        dlm = DataLifecycleManager(knowledge_system=ks)
        assert dlm._is_mastered("k", 0.5) is False

    def test_is_mastered_by_quality(self):
        ks = _make_knowledge({"k": "x" * 200})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._quality_scores["k"] = 0.8
        assert dlm._is_mastered("k", 0.65) is True

    def test_is_mastered_with_verifier(self):
        ks = _make_knowledge({"k": "x" * 200})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._quality_scores["k"] = 0.8
        verifier = MagicMock()
        vr = MagicMock()
        vr.confidence = 0.9
        verifier.verify.return_value = vr
        dlm.verifier = verifier  # type: ignore[assignment]
        assert dlm._is_mastered("k", 0.65) is True

    def test_is_mastered_verifier_error(self):
        ks = _make_knowledge({"k": "x" * 200})
        dlm = DataLifecycleManager(knowledge_system=ks)
        dlm._quality_scores["k"] = 0.8
        verifier = MagicMock()
        verifier.verify.side_effect = RuntimeError("boom")
        dlm.verifier = verifier  # type: ignore[assignment]
        # Falls back to quality-only
        assert dlm._is_mastered("k", 0.65) is True

    def test_persist_archive_error(self):  # noqa: no tmp_path needed
        dlm = DataLifecycleManager(archive_path="/nonexistent/path/archive.json")
        dlm._archive["k"] = {"value": "v"}
        dlm._persist_archive()  # should not raise
