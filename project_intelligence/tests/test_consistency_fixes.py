"""Regression tests for the five consistency defects."""

from __future__ import annotations

import sqlite3

import pytest

from project_intelligence.db.access import (
    add_source_revision_occurrence,
    finish_scan_run,
    insert_entity,
    insert_source_revision,
    start_scan_run,
    upsert_logical_source,
)
from project_intelligence.db.normalize import normalized_value_hash


def test_typed_hash_no_user_object_collision():
    """float must not hash equal to any user dict shaped like old encoding."""
    h_float = normalized_value_hash(1.0)
    h_user = normalized_value_hash({"__pi_float__": "1"})
    h_typed_like = normalized_value_hash({"t": "float", "v": "1"})
    assert h_float != h_user
    assert h_float != h_typed_like
    assert h_float != normalized_value_hash(1)
    assert h_float != normalized_value_hash("1")
    assert h_float != normalized_value_hash("1.0")


def test_logical_source_immutable_mismatch(conn):
    upsert_logical_source(
        conn, source_id="doc:a.md", type_="document", path="a.md"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable field mismatch"):
        upsert_logical_source(
            conn, source_id="doc:a.md", type_="document", path="other.md"
        )


def test_entity_immutable_mismatch(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="ab" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0",
        resolver_version="1",
    )
    insert_entity(
        conn,
        entity_id="e1",
        type_="document",
        stable_label="doc:a.md",
        first_seen_run_id=run_id,
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="immutable field mismatch"):
        insert_entity(
            conn,
            entity_id="e1",
            type_="file",
            stable_label="doc:a.md",
            first_seen_run_id=run_id,
        )


def test_source_revision_immutable_mismatch(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="cd" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0",
        resolver_version="1",
    )
    upsert_logical_source(
        conn, source_id="doc:b.md", type_="document", path="b.md"
    )
    conn.commit()
    r = insert_source_revision(
        conn,
        logical_source_id="doc:b.md",
        first_seen_commit_sha="cd" * 20,
        blob_sha="blobx" + "0" * 35,
        content_hash="hashx" + "0" * 59,
        first_seen_run_id=run_id,
    )
    assert r.created
    with pytest.raises(sqlite3.IntegrityError, match="immutable field mismatch"):
        insert_source_revision(
            conn,
            logical_source_id="doc:b.md",
            first_seen_commit_sha="cd" * 20,
            blob_sha="blobx" + "0" * 35,
            content_hash="DIFFERENT" + "0" * 55,
            first_seen_run_id=run_id,
        )


def test_scan_runs_stats_json_valid(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO scan_runs (
                id, started_at, repository_commit_sha, worktree_state,
                schema_version, extractor_version, resolver_version,
                status, stats
            ) VALUES (
                'badstats', datetime('now'), 'ee' || replace(hex(zeroblob(19)),'00','e'),
                'clean', '1', '0', '1', 'running', 'not-json'
            )
            """
        )


def test_entity_resolved_has_no_resolved_value_column(conn):
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(entity_resolved)").fetchall()
    }
    assert "resolved_value" not in cols
    assert "winning_claim_id" in cols


def test_start_scan_run_rejects_in_transaction(conn):
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError, match="refuses to commit"):
        start_scan_run(
            conn,
            repository_commit_sha="ff" * 20,
            repository_ref="main",
            worktree_state="clean",
            schema_version="1",
            extractor_version="0",
            resolver_version="1",
        )
    conn.execute("ROLLBACK")


def test_finish_scan_run_rejects_in_transaction(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="11" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0",
        resolver_version="1",
    )
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError, match="refuses to commit"):
        finish_scan_run(conn, run_id, status="success")
    conn.execute("ROLLBACK")


def test_same_blob_two_commits_one_revision(conn):
    """Unchanged file across commits: one source_revision, two occurrences."""
    blob = "sameblob" + "0" * 32
    content = "samecontent" + "0" * 52
    upsert_logical_source(
        conn, source_id="doc:stable.md", type_="document", path="stable.md"
    )
    conn.commit()
    run1 = start_scan_run(
        conn,
        repository_commit_sha="commit1" + "0" * 33,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0",
        resolver_version="1",
    )
    r1 = insert_source_revision(
        conn,
        logical_source_id="doc:stable.md",
        first_seen_commit_sha="commit1" + "0" * 33,
        blob_sha=blob,
        content_hash=content,
        first_seen_run_id=run1,
    )
    assert r1.created is True
    add_source_revision_occurrence(conn, r1.id, run1)
    conn.commit()

    run2 = start_scan_run(
        conn,
        repository_commit_sha="commit2" + "0" * 33,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0",
        resolver_version="1",
    )
    r2 = insert_source_revision(
        conn,
        logical_source_id="doc:stable.md",
        first_seen_commit_sha="commit2" + "0" * 33,  # different commit, same blob
        blob_sha=blob,
        content_hash=content,
        first_seen_run_id=run2,
    )
    assert r2.created is False
    assert r2.id == r1.id
    add_source_revision_occurrence(conn, r2.id, run2)
    conn.commit()

    n_rev = conn.execute(
        "SELECT COUNT(*) AS c FROM source_revisions WHERE logical_source_id = 'doc:stable.md'"
    ).fetchone()["c"]
    assert n_rev == 1
    n_occ = conn.execute(
        "SELECT COUNT(*) AS c FROM source_revision_occurrences WHERE source_revision_id = ?",
        (r1.id,),
    ).fetchone()["c"]
    assert n_occ == 2
    first = conn.execute(
        "SELECT first_seen_commit_sha FROM source_revisions WHERE id = ?",
        (r1.id,),
    ).fetchone()["first_seen_commit_sha"]
    assert first == "commit1" + "0" * 33
