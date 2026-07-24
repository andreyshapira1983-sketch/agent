"""DB access layer: scan_run lifecycle, idempotent inserts, append-only helpers.

Transactional model:
- start_scan_run / finish_scan_run require connection NOT in a transaction
  (they perform their own short commit). Callers mid-import must use a
  separate meta connection for status updates, or finish after import txn.
- import_transaction: import work in one txn; on failure before COMMIT →
  ROLLBACK then mark failed (via finish on same conn only if not mid-txn —
  finish is called after ROLLBACK so conn is clean).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, NamedTuple, Optional

from project_intelligence.db.normalize import deterministic_id, normalized_value_hash


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class InsertResult(NamedTuple):
    id: str
    created: bool


def _require_not_in_transaction(conn: sqlite3.Connection, fn: str) -> None:
    if conn.in_transaction:
        raise RuntimeError(
            f"{fn} refuses to commit while connection is in a transaction; "
            "use a dedicated meta connection or call outside import_transaction"
        )


# ---------------------------------------------------------------------------
# Scan run metadata (own short transactions only)
# ---------------------------------------------------------------------------

def start_scan_run(
    conn: sqlite3.Connection,
    *,
    repository_commit_sha: str,
    repository_ref: Optional[str],
    worktree_state: str,
    schema_version: str,
    extractor_version: str,
    resolver_version: str,
    dirty_paths_hash: Optional[str] = None,
) -> str:
    """Insert running scan_run and commit. Rejects if conn already in a txn."""
    _require_not_in_transaction(conn, "start_scan_run")
    run_id = new_id()
    conn.execute(
        """
        INSERT INTO scan_runs (
            id, started_at, repository_commit_sha, repository_ref,
            worktree_state, dirty_paths_hash, schema_version,
            extractor_version, resolver_version, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            run_id,
            _utcnow(),
            repository_commit_sha,
            repository_ref,
            worktree_state,
            dirty_paths_hash,
            schema_version,
            extractor_version,
            resolver_version,
        ),
    )
    conn.commit()
    return run_id


def finish_scan_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    stats: Optional[dict] = None,
    error_summary: Optional[str] = None,
) -> None:
    """Update terminal status and commit. Rejects if conn already in a txn."""
    _require_not_in_transaction(conn, "finish_scan_run")
    if status not in ("success", "failed"):
        raise ValueError("status must be success or failed")
    conn.execute(
        """
        UPDATE scan_runs
        SET finished_at = ?, status = ?, stats = ?, error_summary = ?
        WHERE id = ?
        """,
        (
            _utcnow(),
            status,
            json.dumps(stats) if stats is not None else None,
            error_summary,
            run_id,
        ),
    )
    conn.commit()


def recover_stale_running_runs(
    conn: sqlite3.Connection,
    *,
    error_summary: str = "recovered: left in running state",
) -> list[str]:
    _require_not_in_transaction(conn, "recover_stale_running_runs")
    rows = conn.execute(
        "SELECT id FROM scan_runs WHERE status = 'running'"
    ).fetchall()
    ids = [r["id"] for r in rows]
    for run_id in ids:
        finish_scan_run(
            conn,
            run_id,
            status="failed",
            error_summary=error_summary,
        )
    return ids


@contextmanager
def import_transaction(
    conn: sqlite3.Connection,
    run_id: str,
) -> Generator[sqlite3.Connection, None, None]:
    """Import transaction with correct post-COMMIT error handling."""
    committed = False
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
        committed = True
    except Exception as exc:
        if not committed:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            try:
                finish_scan_run(
                    conn,
                    run_id,
                    status="failed",
                    error_summary=str(exc)[:2000],
                )
            except Exception:
                pass
        raise

    try:
        finish_scan_run(conn, run_id, status="success", stats={"ok": True})
    except Exception as finish_exc:
        try:
            row = conn.execute(
                "SELECT status FROM scan_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row and row["status"] == "running":
                finish_scan_run(
                    conn,
                    run_id,
                    status="failed",
                    error_summary=f"finish_scan_run failed: {finish_exc}"[:2000],
                )
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Idempotent inserts with immutable-field checks
# ---------------------------------------------------------------------------

def upsert_logical_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    type_: str,
    path: str,
    classification: Optional[str] = None,
    authority_scope: Optional[str] = None,
    title_hint: Optional[str] = None,
) -> InsertResult:
    cur = conn.execute(
        """
        INSERT INTO logical_sources
            (id, type, path, classification, authority_scope, title_hint)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (source_id, type_, path, classification, authority_scope, title_hint),
    )
    if cur.rowcount > 0:
        return InsertResult(source_id, True)
    row = conn.execute(
        "SELECT id, type, path FROM logical_sources WHERE id = ?", (source_id,)
    ).fetchone()
    if row is None:
        # UNIQUE(type, path) conflict under different id
        row2 = conn.execute(
            "SELECT id, type, path FROM logical_sources WHERE type = ? AND path = ?",
            (type_, path),
        ).fetchone()
        if row2 is None:
            raise sqlite3.IntegrityError("logical_sources insert failed")
        raise sqlite3.IntegrityError(
            f"logical_sources UNIQUE(type,path) owned by id={row2['id']!r}, "
            f"not {source_id!r}"
        )
    if row["type"] != type_ or row["path"] != path:
        raise sqlite3.IntegrityError(
            "logical_sources immutable field mismatch for existing id: "
            f"stored type/path=({row['type']!r},{row['path']!r}) "
            f"vs ({type_!r},{path!r})"
        )
    return InsertResult(row["id"], False)


def insert_source_revision(
    conn: sqlite3.Connection,
    *,
    logical_source_id: str,
    first_seen_commit_sha: str,
    blob_sha: str,
    content_hash: str,
    first_seen_run_id: str,
    temporal_scope: str = "current",
    size_bytes: Optional[int] = None,
    revision_id: Optional[str] = None,
) -> InsertResult:
    """Idempotent on UNIQUE(logical_source_id, blob_sha).

    Same blob content across different repository commits reuses one revision.
    The commit that first introduced the blob is stored as first_seen_commit_sha
    and is NOT compared on subsequent inserts. Per-run presence is recorded via
    source_revision_occurrences + scan_runs.repository_commit_sha.
    """
    rid = revision_id or deterministic_id(
        logical_source_id, blob_sha, prefix="rev:"
    )
    cur = conn.execute(
        """
        INSERT INTO source_revisions (
            id, logical_source_id, first_seen_commit_sha, blob_sha, content_hash,
            temporal_scope, first_seen_run_id, size_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(logical_source_id, blob_sha) DO NOTHING
        """,
        (
            rid,
            logical_source_id,
            first_seen_commit_sha,
            blob_sha,
            content_hash,
            temporal_scope,
            first_seen_run_id,
            size_bytes,
        ),
    )
    if cur.rowcount > 0:
        return InsertResult(rid, True)
    row = conn.execute(
        """
        SELECT id, logical_source_id, blob_sha, content_hash, temporal_scope
        FROM source_revisions
        WHERE logical_source_id = ? AND blob_sha = ?
        """,
        (logical_source_id, blob_sha),
    ).fetchone()
    if row is None:
        row2 = conn.execute(
            "SELECT id, logical_source_id, blob_sha FROM source_revisions WHERE id = ?",
            (rid,),
        ).fetchone()
        if row2 is None:
            raise sqlite3.IntegrityError("source_revisions conflict without row")
        raise sqlite3.IntegrityError(
            "source_revisions id exists with different natural key"
        )
    if row["content_hash"] != content_hash:
        raise sqlite3.IntegrityError(
            "source_revisions immutable field mismatch for existing "
            "(logical_source_id, blob_sha): content_hash differs"
        )
    if row["temporal_scope"] != temporal_scope:
        raise sqlite3.IntegrityError(
            "source_revisions immutable field mismatch: temporal_scope differs"
        )
    return InsertResult(row["id"], False)


def add_source_revision_occurrence(
    conn: sqlite3.Connection,
    source_revision_id: str,
    scan_run_id: str,
) -> InsertResult:
    cur = conn.execute(
        """
        INSERT INTO source_revision_occurrences
            (source_revision_id, scan_run_id, observed_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source_revision_id, scan_run_id) DO NOTHING
        """,
        (source_revision_id, scan_run_id, _utcnow()),
    )
    return InsertResult(f"{source_revision_id}:{scan_run_id}", cur.rowcount > 0)


def insert_extraction_candidate(
    conn: sqlite3.Connection,
    *,
    scan_run_id: str,
    source_revision_id: str,
    extractor_id: str,
    extractor_version: str,
    candidate_type: str,
    raw_title: str,
    value_for_hash: Any,
    line_start: int,
    line_end: int,
    confidence: float = 1.0,
    raw_payload: Optional[dict] = None,
    heading_path: Optional[list] = None,
    excerpt: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> InsertResult:
    nvh = normalized_value_hash(value_for_hash)
    cid = candidate_id or deterministic_id(
        source_revision_id,
        str(line_start),
        str(line_end),
        extractor_id,
        extractor_version,
        candidate_type,
        nvh,
        prefix="cand:",
    )
    cur = conn.execute(
        """
        INSERT INTO extraction_candidates (
            id, scan_run_id, source_revision_id, extractor_id, extractor_version,
            candidate_type, raw_title, normalized_value_hash, raw_payload,
            line_start, line_end, heading_path, excerpt, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            source_revision_id, line_start, line_end,
            extractor_id, extractor_version, candidate_type, normalized_value_hash
        ) DO NOTHING
        """,
        (
            cid,
            scan_run_id,
            source_revision_id,
            extractor_id,
            extractor_version,
            candidate_type,
            raw_title,
            nvh,
            json.dumps(raw_payload) if raw_payload is not None else None,
            line_start,
            line_end,
            json.dumps(heading_path) if heading_path is not None else None,
            excerpt,
            confidence,
        ),
    )
    if cur.rowcount > 0:
        return InsertResult(cid, True)
    row = conn.execute(
        """
        SELECT id FROM extraction_candidates
        WHERE source_revision_id = ? AND line_start = ? AND line_end = ?
          AND extractor_id = ? AND extractor_version = ?
          AND candidate_type = ? AND normalized_value_hash = ?
        """,
        (
            source_revision_id,
            line_start,
            line_end,
            extractor_id,
            extractor_version,
            candidate_type,
            nvh,
        ),
    ).fetchone()
    if not row:
        raise sqlite3.IntegrityError("extraction_candidates conflict without row")
    return InsertResult(row["id"], False)


def insert_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    type_: str,
    stable_label: str,
    first_seen_run_id: str,
) -> InsertResult:
    cur = conn.execute(
        """
        INSERT INTO entities
            (id, type, stable_label, first_seen_run_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (entity_id, type_, stable_label, first_seen_run_id, _utcnow()),
    )
    if cur.rowcount > 0:
        return InsertResult(entity_id, True)
    row = conn.execute(
        "SELECT id, type, stable_label FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if not row:
        raise sqlite3.IntegrityError("entities conflict without row")
    if row["type"] != type_ or row["stable_label"] != stable_label:
        raise sqlite3.IntegrityError(
            "entities immutable field mismatch for existing id: "
            f"stored type/stable_label=({row['type']!r},{row['stable_label']!r}) "
            f"vs ({type_!r},{stable_label!r})"
        )
    return InsertResult(row["id"], False)


def insert_entity_claim(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim_type: str,
    claim_value: Any,
    source_class: str,
    source_revision_id: str,
    scan_run_id: str,
    extractor_id: str,
    extractor_version: str,
    line_start: int,
    line_end: int,
    subsystem: str = "",
    temporal_scope: str = "current",
    observation_id: Optional[str] = None,
    confidence: float = 1.0,
    heading_path: Optional[list] = None,
    excerpt: Optional[str] = None,
    claim_id: Optional[str] = None,
) -> InsertResult:
    nvh = normalized_value_hash(claim_value)
    cid = claim_id or deterministic_id(
        entity_id,
        claim_type,
        subsystem,
        source_revision_id,
        str(line_start),
        str(line_end),
        extractor_id,
        extractor_version,
        nvh,
        prefix="claim:",
    )
    cur = conn.execute(
        """
        INSERT INTO entity_claims (
            id, entity_id, claim_type, claim_value, normalized_value_hash,
            subsystem, temporal_scope, source_class, source_revision_id,
            observation_id, scan_run_id, extractor_id, extractor_version,
            line_start, line_end, heading_path, excerpt, confidence, asserted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            entity_id, claim_type, subsystem,
            source_revision_id, line_start, line_end,
            extractor_id, extractor_version, normalized_value_hash
        ) DO NOTHING
        """,
        (
            cid,
            entity_id,
            claim_type,
            json.dumps(claim_value),
            nvh,
            subsystem,
            temporal_scope,
            source_class,
            source_revision_id,
            observation_id,
            scan_run_id,
            extractor_id,
            extractor_version,
            line_start,
            line_end,
            json.dumps(heading_path) if heading_path is not None else None,
            excerpt,
            confidence,
            _utcnow(),
        ),
    )
    if cur.rowcount > 0:
        return InsertResult(cid, True)
    row = conn.execute(
        """
        SELECT id FROM entity_claims
        WHERE entity_id = ? AND claim_type = ? AND subsystem = ?
          AND source_revision_id = ? AND line_start = ? AND line_end = ?
          AND extractor_id = ? AND extractor_version = ?
          AND normalized_value_hash = ?
        """,
        (
            entity_id,
            claim_type,
            subsystem,
            source_revision_id,
            line_start,
            line_end,
            extractor_id,
            extractor_version,
            nvh,
        ),
    ).fetchone()
    if not row:
        raise sqlite3.IntegrityError("entity_claims conflict without row")
    return InsertResult(row["id"], False)


def insert_observation(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    source_revision_id: str,
    scan_run_id: str,
    extractor_id: str,
    extractor_version: str,
    value_for_hash: Any,
    line_start: int,
    line_end: int,
    confidence: float = 1.0,
    role: Optional[str] = None,
    from_candidate_id: Optional[str] = None,
    heading_path: Optional[list] = None,
    excerpt: Optional[str] = None,
    observation_id: Optional[str] = None,
) -> InsertResult:
    nvh = normalized_value_hash(value_for_hash)
    oid = observation_id or deterministic_id(
        entity_id,
        source_revision_id,
        str(line_start),
        str(line_end),
        extractor_id,
        extractor_version,
        nvh,
        prefix="obs:",
    )
    cur = conn.execute(
        """
        INSERT INTO observations (
            id, entity_id, source_revision_id, scan_run_id,
            extractor_id, extractor_version, normalized_value_hash,
            line_start, line_end, heading_path, excerpt, confidence,
            role, from_candidate_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            entity_id, source_revision_id, line_start, line_end,
            extractor_id, extractor_version, normalized_value_hash
        ) DO NOTHING
        """,
        (
            oid,
            entity_id,
            source_revision_id,
            scan_run_id,
            extractor_id,
            extractor_version,
            nvh,
            line_start,
            line_end,
            json.dumps(heading_path) if heading_path is not None else None,
            excerpt,
            confidence,
            role,
            from_candidate_id,
        ),
    )
    if cur.rowcount > 0:
        return InsertResult(oid, True)
    row = conn.execute(
        """
        SELECT id FROM observations
        WHERE entity_id = ? AND source_revision_id = ?
          AND line_start = ? AND line_end = ?
          AND extractor_id = ? AND extractor_version = ?
          AND normalized_value_hash = ?
        """,
        (
            entity_id,
            source_revision_id,
            line_start,
            line_end,
            extractor_id,
            extractor_version,
            nvh,
        ),
    ).fetchone()
    if not row:
        raise sqlite3.IntegrityError("observations conflict without row")
    return InsertResult(row["id"], False)


def count_table(conn: sqlite3.Connection, table: str) -> int:
    allowed = {
        "logical_sources",
        "source_revisions",
        "extraction_candidates",
        "entities",
        "entity_claims",
        "observations",
        "scan_runs",
        "schema_migrations",
    }
    if table not in allowed:
        raise ValueError(f"table not allowed: {table}")
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"])
