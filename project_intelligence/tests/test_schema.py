"""Schema constraints, FK, CHECK, append-only triggers."""

from __future__ import annotations

import json
import sqlite3

import pytest

from project_intelligence.db.access import (
    insert_entity,
    insert_entity_claim,
    start_scan_run,
    upsert_logical_source,
)
from project_intelligence.db.connection import connect, get_schema_version
from project_intelligence.db.migrate import apply_migrations


def _seed_minimal(conn: sqlite3.Connection) -> dict:
    run_id = start_scan_run(
        conn,
        repository_commit_sha="a" * 40,
        repository_ref="main",
        worktree_state="clean",
        schema_version="1",
        extractor_version="0.0.0",
        resolver_version="1",
    )
    upsert_logical_source(
        conn, source_id="doc:docs/ROADMAP.md", type_="document", path="docs/ROADMAP.md"
    )
    conn.execute(
        """
        INSERT INTO source_revisions (
            id, logical_source_id, first_seen_commit_sha, blob_sha, content_hash,
            temporal_scope, first_seen_run_id, size_bytes
        ) VALUES (?, ?, ?, ?, ?, 'current', ?, 10)
        """,
        ("rev1", "doc:docs/ROADMAP.md", "a" * 40, "b" * 40, "c" * 64, run_id),
    )
    conn.commit()
    insert_entity(
        conn,
        entity_id="entity:1",
        type_="document",
        stable_label="doc:docs/ROADMAP.md",
        first_seen_run_id=run_id,
    )
    conn.commit()
    return {"run_id": run_id, "revision_id": "rev1", "entity_id": "entity:1"}


def test_pragma_foreign_keys_on(conn):
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert int(row[0]) == 1


def test_schema_version(db_path):
    c = connect(db_path)
    assert get_schema_version(c) == "1"
    c.close()


def test_migrations_idempotent(db_path):
    again = apply_migrations(db_path)
    assert again == []
    c = connect(db_path)
    assert get_schema_version(c) == "1"
    c.close()


def test_check_line_start_ge_1(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO observations (
                id, entity_id, source_revision_id, scan_run_id,
                extractor_id, extractor_version, normalized_value_hash,
                line_start, line_end, confidence
            ) VALUES ('obs-bad0', ?, ?, ?, 'e', '1', 'h', 0, 1, 1.0)
            """,
            (s["entity_id"], s["revision_id"], s["run_id"]),
        )


def test_check_line_start_le_line_end(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO observations (
                id, entity_id, source_revision_id, scan_run_id,
                extractor_id, extractor_version, normalized_value_hash,
                line_start, line_end, confidence
            ) VALUES ('obs-bad', ?, ?, ?, 'e', '1', 'h', 10, 5, 1.0)
            """,
            (s["entity_id"], s["revision_id"], s["run_id"]),
        )


def test_check_confidence_range(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO observations (
                id, entity_id, source_revision_id, scan_run_id,
                extractor_id, extractor_version, normalized_value_hash,
                line_start, line_end, confidence
            ) VALUES ('obs-bad2', ?, ?, ?, 'e', '1', 'h', 1, 2, 1.5)
            """,
            (s["entity_id"], s["revision_id"], s["run_id"]),
        )


def test_json_valid_heading_path(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO observations (
                id, entity_id, source_revision_id, scan_run_id,
                extractor_id, extractor_version, normalized_value_hash,
                line_start, line_end, confidence, heading_path
            ) VALUES ('obs-j', ?, ?, ?, 'e', '1', 'h', 1, 1, 1.0, 'not-json')
            """,
            (s["entity_id"], s["revision_id"], s["run_id"]),
        )


def test_edge_candidate_endpoint_xor(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO edge_candidates (
                id, scan_run_id, source_revision_id, extractor_id, extractor_version,
                source_candidate_id, source_entity_id,
                target_entity_id, relation, normalized_value_hash,
                line_start, line_end, confidence
            ) VALUES (
                'ec1', ?, ?, 'ex', '1',
                NULL, NULL,
                ?, 'depends_on', 'hash',
                1, 1, 1.0
            )
            """,
            (s["run_id"], s["revision_id"], s["entity_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO edge_candidates (
                id, scan_run_id, source_revision_id, extractor_id, extractor_version,
                source_candidate_id, source_entity_id,
                target_entity_id, relation, normalized_value_hash,
                line_start, line_end, confidence
            ) VALUES (
                'ec2', ?, ?, 'ex', '1',
                'cand', ?,
                ?, 'depends_on', 'hash2',
                1, 1, 1.0
            )
            """,
            (s["run_id"], s["revision_id"], s["entity_id"], s["entity_id"]),
        )


def test_fk_promoted_entity_id(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO extraction_candidates (
                id, scan_run_id, source_revision_id, extractor_id, extractor_version,
                candidate_type, raw_title, normalized_value_hash,
                line_start, line_end, confidence, promoted_entity_id
            ) VALUES (
                'cand1', ?, ?, 'ex', '1', 'task', 't', 'h', 1, 1, 1.0, 'missing-entity'
            )
            """,
            (s["run_id"], s["revision_id"]),
        )


def test_fk_observation_id_on_claim(conn):
    s = _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert_entity_claim(
            conn,
            claim_id="cl1",
            entity_id=s["entity_id"],
            claim_type="title",
            claim_value="Hello",
            source_class="canonical_doc",
            source_revision_id=s["revision_id"],
            scan_run_id=s["run_id"],
            extractor_id="ex",
            extractor_version="1",
            line_start=1,
            line_end=1,
            observation_id="does-not-exist",
        )


def test_append_only_entity_claims(conn):
    s = _seed_minimal(conn)
    insert_entity_claim(
        conn,
        claim_id="cl2",
        entity_id=s["entity_id"],
        claim_type="title",
        claim_value="Title A",
        source_class="canonical_doc",
        source_revision_id=s["revision_id"],
        scan_run_id=s["run_id"],
        extractor_id="ex",
        extractor_version="1",
        line_start=1,
        line_end=2,
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE entity_claims SET excerpt = 'x' WHERE id = 'cl2'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM entity_claims WHERE id = 'cl2'")


def test_append_only_proposals(conn):
    conn.execute(
        """
        INSERT INTO proposals (id, type, payload, created_at, created_by)
        VALUES ('p1', 'test', '{}', datetime('now'), 'test')
        """
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE proposals SET rationale = 'nope' WHERE id = 'p1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM proposals WHERE id = 'p1'")


def test_authority_rules_nullsafe_unique(conn):
    conn.execute(
        """
        INSERT INTO authority_rules (
            id, resolver_version, claim_type, source_class, priority
        ) VALUES ('ar1', '1', 'status', 'code', 100)
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO authority_rules (
                id, resolver_version, claim_type, source_class, priority
            ) VALUES ('ar2', '1', 'status', 'code', 90)
            """
        )


def test_entities_have_stable_label_not_title(conn):
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(entities)").fetchall()
    }
    assert "stable_label" in cols
    assert "title" not in cols


def test_entity_resolved_winning_claim_consistency(conn):
    s = _seed_minimal(conn)
    r = insert_entity_claim(
        conn,
        claim_id="cl3",
        entity_id=s["entity_id"],
        claim_type="status",
        claim_value="open",
        source_class="canonical_doc",
        source_revision_id=s["revision_id"],
        scan_run_id=s["run_id"],
        extractor_id="ex",
        extractor_version="1",
        line_start=3,
        line_end=3,
        subsystem="memory",
    )
    conn.commit()
    # wrong subsystem → reject
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO entity_resolved (
                entity_id, claim_type, subsystem,
                winning_claim_id, resolution_rule, resolver_version,
                resolved_at, scan_run_id
            ) VALUES (?, 'status', '', ?, 'rule', '1', datetime('now'), ?)
            """,
            (s["entity_id"], r.id, s["run_id"]),
        )
    # matching subsystem → ok; value via JOIN on winning_claim_id
    conn.execute(
        """
        INSERT INTO entity_resolved (
            entity_id, claim_type, subsystem,
            winning_claim_id, resolution_rule, resolver_version,
            resolved_at, scan_run_id
        ) VALUES (?, 'status', 'memory', ?, 'rule', '1', datetime('now'), ?)
        """,
        (s["entity_id"], r.id, s["run_id"]),
    )
    conn.commit()
    joined = conn.execute(
        """
        SELECT c.claim_value AS resolved_value
        FROM entity_resolved er
        JOIN entity_claims c ON c.id = er.winning_claim_id
        WHERE er.entity_id = ? AND er.claim_type = 'status' AND er.subsystem = 'memory'
        """,
        (s["entity_id"],),
    ).fetchone()
    assert json.loads(joined["resolved_value"]) == "open"

