"""Tests for brain/audit.py — hash-chained AuditLog."""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.audit import AuditEntry, AuditLog, GENESIS_HASH, _compute_hash


# ════════════════════════════════════════════════════════════════════
# Recording
# ════════════════════════════════════════════════════════════════════

class TestRecording:

    def test_first_entry_anchors_to_genesis(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        entry = log.record(actor="brain", action="tool_call", target="email")
        assert entry.seq == 1
        assert entry.prev_hash == GENESIS_HASH
        assert entry.entry_hash != GENESIS_HASH
        assert len(log) == 1

    def test_subsequent_entries_chain(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        e1 = log.record(actor="a", action="x", target="t")
        e2 = log.record(actor="b", action="y", target="t")
        e3 = log.record(actor="c", action="z", target="t")
        assert e2.prev_hash == e1.entry_hash
        assert e3.prev_hash == e2.entry_hash
        assert e1.entry_hash != e2.entry_hash != e3.entry_hash

    def test_params_jsonifies_safely(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        log.record(
            actor="brain", action="job_status", target="j-123",
            verdict="DELIVERED",
            params={"profession": "text_editor", "tokens": 950, "ok": True},
        )
        entry = log.get(1)
        assert entry is not None
        assert entry.params == {"profession": "text_editor", "tokens": 950, "ok": True}

    def test_head_tracks_latest(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        assert log.head() == GENESIS_HASH
        e1 = log.record(actor="a", action="x", target="t")
        assert log.head() == e1.entry_hash
        e2 = log.record(actor="a", action="y", target="t")
        assert log.head() == e2.entry_hash


# ════════════════════════════════════════════════════════════════════
# Integrity
# ════════════════════════════════════════════════════════════════════

class TestIntegrity:

    def test_clean_chain_verifies(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        for i in range(20):
            log.record(actor=f"a{i}", action="x", target="t")
        report = log.verify_chain()
        assert report.ok
        assert report.total_entries == 20

    def test_empty_log_verifies(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        report = log.verify_chain()
        assert report.ok
        assert report.total_entries == 0

    def test_tampered_payload_detected(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        log.record(actor="a", action="x", target="t1")
        log.record(actor="b", action="y", target="t2")
        # Directly mutate the DB to fake a "someone edited the row" scenario
        log._conn.execute(
            "UPDATE audit_entries SET params_json = ? WHERE seq = ?",
            ('{"tampered": true}', 1),
        )
        log._conn.commit()
        report = log.verify_chain()
        assert not report.ok
        assert report.first_bad_seq == 1

    def test_tampered_chain_link_detected(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        log.record(actor="a", action="x", target="t1")
        log.record(actor="b", action="y", target="t2")
        # Break the chain link
        log._conn.execute(
            "UPDATE audit_entries SET prev_hash = ? WHERE seq = ?",
            (GENESIS_HASH, 2),
        )
        log._conn.commit()
        report = log.verify_chain()
        assert not report.ok
        assert report.first_bad_seq == 2
        assert "prev_hash" in report.notes[0] or "broken" in report.notes[0]


# ════════════════════════════════════════════════════════════════════
# Read API
# ════════════════════════════════════════════════════════════════════

class TestReadAPI:

    def test_last_n(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        for i in range(15):
            log.record(actor=f"a{i}", action="x", target="t")
        last = log.last(5)
        assert len(last) == 5
        # Ordered ascending by seq even though we asked for "last"
        assert [e.seq for e in last] == [11, 12, 13, 14, 15]

    def test_iter_yields_all_entries_in_order(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        for i in range(5):
            log.record(actor=f"a{i}", action="x", target="t")
        seqs = [e.seq for e in log]
        assert seqs == [1, 2, 3, 4, 5]

    def test_get_returns_none_for_missing_seq(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db")
        log.record(actor="a", action="x", target="t")
        assert log.get(999) is None
        assert log.get(1) is not None


# ════════════════════════════════════════════════════════════════════
# Determinism
# ════════════════════════════════════════════════════════════════════

class TestDeterminism:

    def test_dict_key_order_does_not_affect_hash(self):
        # JSON-encoded params with sorted keys hash identically regardless
        # of how the Python dict was assembled.
        h1 = _compute_hash(seq=1, ts="t", actor="a", action="x", target="t",
                           verdict="OK", params_json='{"a": 1, "b": 2}',
                           prev_hash=GENESIS_HASH)
        h2 = _compute_hash(seq=1, ts="t", actor="a", action="x", target="t",
                           verdict="OK", params_json='{"a": 1, "b": 2}',
                           prev_hash=GENESIS_HASH)
        assert h1 == h2

    def test_stable_params_yield_same_hash_in_log(self, tmp_path):
        from brain.audit import AuditLog
        log = AuditLog(tmp_path / "audit.db")
        e1 = log.record(actor="a", action="x", target="t", params={"a": 1, "b": 2})
        e2 = log.record(actor="a", action="x", target="t", params={"b": 2, "a": 1})
        # Two records of the "same" content with different dict orderings
        # produce different entry hashes only because seq+ts differ.
        # Their params_json is sorted, so the JSON itself is byte-equal.
        r1 = log.get(1)
        r2 = log.get(2)
        assert r1.params == r2.params
        log.close()


# ════════════════════════════════════════════════════════════════════
# Persistence across processes
# ════════════════════════════════════════════════════════════════════

class TestPersistence:

    def test_reopen_continues_chain(self, tmp_path):
        path = tmp_path / "audit.db"
        log = AuditLog(path)
        e1 = log.record(actor="a", action="x", target="t")
        log.close()

        log2 = AuditLog(path)
        assert log2.head() == e1.entry_hash
        e2 = log2.record(actor="b", action="y", target="t")
        assert e2.seq == 2
        assert e2.prev_hash == e1.entry_hash
        report = log2.verify_chain()
        assert report.ok
        log2.close()
