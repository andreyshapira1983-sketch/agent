"""Idempotency keys and split scan_run transactions."""

from __future__ import annotations

import sqlite3

import pytest

from project_intelligence.db.access import (
    add_source_revision_occurrence,
    count_table,
    finish_scan_run,
    import_transaction,
    insert_entity,
    insert_entity_claim,
    insert_extraction_candidate,
    insert_observation,
    insert_source_revision,
    recover_stale_running_runs,
    start_scan_run,
    upsert_logical_source,
)
from project_intelligence.db.normalize import canonicalize, normalized_value_hash


def _base(conn: sqlite3.Connection) -> dict:
    run_id = start_scan_run(
        conn,
        repository_commit_sha="deadbeef" * 5,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    upsert_logical_source(
        conn, source_id="doc:docs/X.md", type_="document", path="docs/X.md"
    )
    conn.commit()
    r = insert_source_revision(
        conn,
        logical_source_id="doc:docs/X.md",
        first_seen_commit_sha="deadbeef" * 5,
        blob_sha="blob" + "0" * 36,
        content_hash="content" + "0" * 57,
        first_seen_run_id=run_id,
    )
    assert r.created is True
    add_source_revision_occurrence(conn, r.id, run_id)
    conn.commit()
    e = insert_entity(
        conn,
        entity_id="ent-x",
        type_="document",
        stable_label="doc:docs/X.md",
        first_seen_run_id=run_id,
    )
    assert e.created is True
    conn.commit()
    return {"run_id": run_id, "revision_id": r.id, "entity_id": e.id}


def test_normalized_value_hash_stable():
    a = normalized_value_hash({"b": 1, "a": "  Hello\nWorld  "})
    b = normalized_value_hash({"a": "Hello World", "b": 1})
    assert a == b
    assert len(a) == 64


def test_canonicalize_rejects_nan():
    with pytest.raises(ValueError):
        canonicalize(float("nan"))


def test_source_revision_idempotent_returns_existing_id(conn):
    s = _base(conn)
    again = insert_source_revision(
        conn,
        revision_id="rev-x-dup-id",
        logical_source_id="doc:docs/X.md",
        first_seen_commit_sha="deadbeef" * 5,
        blob_sha="blob" + "0" * 36,
        content_hash="content" + "0" * 57,
        first_seen_run_id=s["run_id"],
    )
    assert again.created is False
    assert again.id == s["revision_id"]
    assert count_table(conn, "source_revisions") == 1


def test_extraction_candidate_idempotent(conn):
    s = _base(conn)
    payload = {"title": "Task A", "kind": "task"}
    r1 = insert_extraction_candidate(
        conn,
        candidate_id="cand-1",
        scan_run_id=s["run_id"],
        source_revision_id=s["revision_id"],
        extractor_id="roadmap",
        extractor_version="1",
        candidate_type="task",
        raw_title="Task A",
        value_for_hash=payload,
        line_start=10,
        line_end=12,
    )
    r2 = insert_extraction_candidate(
        conn,
        candidate_id="cand-2",
        scan_run_id=s["run_id"],
        source_revision_id=s["revision_id"],
        extractor_id="roadmap",
        extractor_version="1",
        candidate_type="task",
        raw_title="Task A",
        value_for_hash=payload,
        line_start=10,
        line_end=12,
    )
    assert r1.created is True
    assert r2.created is False
    assert r2.id == r1.id
    assert count_table(conn, "extraction_candidates") == 1


def test_fk_violation_not_silenced(conn):
    s = _base(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert_extraction_candidate(
            conn,
            scan_run_id=s["run_id"],
            source_revision_id="missing-revision",
            extractor_id="roadmap",
            extractor_version="1",
            candidate_type="task",
            raw_title="X",
            value_for_hash={"t": "x"},
            line_start=1,
            line_end=1,
        )


def test_entity_claim_idempotent_includes_subsystem(conn):
    s = _base(conn)
    kwargs = dict(
        entity_id=s["entity_id"],
        claim_type="status",
        claim_value="open",
        source_class="canonical_doc",
        source_revision_id=s["revision_id"],
        scan_run_id=s["run_id"],
        extractor_id="ex",
        extractor_version="1",
        line_start=1,
        line_end=1,
        subsystem="memory",
    )
    r1 = insert_entity_claim(conn, claim_id="c1", **kwargs)
    r2 = insert_entity_claim(conn, claim_id="c2", **kwargs)
    assert r1.created is True
    assert r2.created is False
    assert r2.id == r1.id
    kwargs2 = {**kwargs, "subsystem": "planner"}
    r3 = insert_entity_claim(conn, claim_id="c3", **kwargs2)
    assert r3.created is True
    assert count_table(conn, "entity_claims") == 2


def test_observation_idempotent(conn):
    s = _base(conn)
    val = {"mention": "ROADMAP"}
    r1 = insert_observation(
        conn,
        observation_id="o1",
        entity_id=s["entity_id"],
        source_revision_id=s["revision_id"],
        scan_run_id=s["run_id"],
        extractor_id="ex",
        extractor_version="1",
        value_for_hash=val,
        line_start=5,
        line_end=5,
    )
    r2 = insert_observation(
        conn,
        observation_id="o2",
        entity_id=s["entity_id"],
        source_revision_id=s["revision_id"],
        scan_run_id=s["run_id"],
        extractor_id="ex",
        extractor_version="1",
        value_for_hash=val,
        line_start=5,
        line_end=5,
    )
    assert r1.created is True
    assert r2.created is False
    assert r2.id == r1.id
    assert count_table(conn, "observations") == 1


def test_failed_import_preserves_scan_run(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="ff" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    with pytest.raises(RuntimeError, match="boom"), import_transaction(conn, run_id):
        conn.execute(
            """
                INSERT INTO logical_sources (id, type, path)
                VALUES ('doc:tmp.md', 'document', 'tmp.md')
                """
        )
        raise RuntimeError("boom")
    row = conn.execute(
        "SELECT status, error_summary FROM scan_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "boom" in (row["error_summary"] or "")
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM logical_sources WHERE id = 'doc:tmp.md'"
    ).fetchone()["c"]
    assert n == 0


def test_successful_import_marks_success(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="aa" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    with import_transaction(conn, run_id):
        upsert_logical_source(
            conn, source_id="doc:ok.md", type_="document", path="ok.md"
        )
    row = conn.execute(
        "SELECT status FROM scan_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "success"


def test_post_commit_error_does_not_rollback_import(conn, monkeypatch):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="bb" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    calls = {"n": 0}
    real_finish = finish_scan_run

    def flaky_finish(c, rid, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1 and kwargs.get("status") == "success":
            raise RuntimeError("finish failed")
        return real_finish(c, rid, **kwargs)

    monkeypatch.setattr(
        "project_intelligence.db.access.finish_scan_run", flaky_finish
    )
    with pytest.raises(RuntimeError, match="finish failed"), import_transaction(conn, run_id):
        upsert_logical_source(
            conn, source_id="doc:post.md", type_="document", path="post.md"
        )
    # import data must remain
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM logical_sources WHERE id = 'doc:post.md'"
    ).fetchone()["c"]
    assert n == 1


def test_recover_stale_running_runs(conn):
    run_id = start_scan_run(
        conn,
        repository_commit_sha="cc" * 20,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    row = conn.execute(
        "SELECT status FROM scan_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "running"
    recovered = recover_stale_running_runs(conn)
    assert run_id in recovered
    row = conn.execute(
        "SELECT status FROM scan_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "failed"


def test_occurrence_on_rescan(conn):
    s = _base(conn)
    run2 = start_scan_run(
        conn,
        repository_commit_sha="deadbeef" * 5,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    add_source_revision_occurrence(conn, s["revision_id"], run2)
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM source_revision_occurrences WHERE source_revision_id = ?",
        (s["revision_id"],),
    ).fetchone()["c"]
    assert n == 2
    rev = conn.execute(
        "SELECT first_seen_run_id FROM source_revisions WHERE id = ?",
        (s["revision_id"],),
    ).fetchone()
    assert rev["first_seen_run_id"] == s["run_id"]
