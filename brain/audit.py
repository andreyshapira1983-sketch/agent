"""
brain/audit.py — Append-only audit log with hash-chain integrity.

Every consequential decision the agent makes (policy verdict, tool
execution, job state transition, money-spending event) is recorded as a
single immutable row whose `prev_hash` is the previous row's hash. The
chain anchors at a fixed genesis hash so any tampering — even at row
zero — surfaces immediately when `verify_chain()` walks the log.

Storage: SQLite with a single `audit_entries` table. Schema is
intentionally small (no foreign keys, no joins) so the file can be moved
around or rotated independently of the rest of the agent's data.

Threading: each `AuditLog` opens its own connection. We use a thread
lock around writes because SQLite is fine with concurrent reads but only
one writer at a time.

Privacy: the log stores hashed snapshots, not raw payloads. Callers may
pass arbitrary `params`; they're JSON-encoded and SHA-256'd into the
chain, but the JSON is also stored (so investigators can reconstruct
events offline). For sensitive payloads, redact upstream via PIIRedactor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


GENESIS_HASH = "0" * 64
SCHEMA_VERSION = 1


# ════════════════════════════════════════════════════════════════════
# Data model
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AuditEntry:
    """One immutable audit row."""

    seq:           int                # row number (1-indexed)
    ts:            str                # ISO 8601 UTC
    actor:         str                # "brain" | "tool" | "channel" | ...
    action:        str                # "policy_verdict" | "tool_call" | "job_status" | ...
    target:        str                # tool name, job id, profession id, ...
    verdict:       str                # "ALLOW" | "DENY" | "OK" | "ERROR" | etc.
    params:        dict[str, Any]     # JSON-serialisable context
    prev_hash:     str                # hex SHA-256 of previous row
    entry_hash:    str                # hex SHA-256 of this row's content
    entry_id:      str                # uuid4 — independent of seq, useful for logs


@dataclass
class IntegrityReport:
    """Result of `AuditLog.verify_chain()`."""

    ok:               bool
    total_entries:    int
    first_bad_seq:    int | None = None
    expected_hash:    str | None = None
    actual_hash:      str | None = None
    notes:            list[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════
# AuditLog
# ════════════════════════════════════════════════════════════════════

class AuditLog:
    """Append-only hash-chained audit log.

    Typical usage:

        audit = AuditLog(Path("data/audit.db"))
        audit.record(
            actor="tool", action="tool_call", target="email",
            verdict="OK", params={"recipient": "[EMAIL_1]"},
        )

        # Later, on demand:
        report = audit.verify_chain()
        if not report.ok:
            alert(report)
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ────────────────────────────────────────────────────────────────

    def record(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        verdict: str = "OK",
        params: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append one row. Returns the materialised AuditEntry."""
        params = params or {}
        # JSON dump with sorted keys so the hash is deterministic regardless
        # of dict insertion order.
        params_json = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)

        with self._lock:
            prev_hash = self._latest_hash()
            seq = self._next_seq()
            ts = datetime.now(timezone.utc).isoformat()
            entry_id = uuid.uuid4().hex[:16]
            entry_hash = _compute_hash(
                seq=seq, ts=ts, actor=actor, action=action,
                target=target, verdict=verdict, params_json=params_json,
                prev_hash=prev_hash,
            )

            self._conn.execute(
                """
                INSERT INTO audit_entries
                    (seq, ts, actor, action, target, verdict, params_json,
                     prev_hash, entry_hash, entry_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq, ts, actor, action, target, verdict, params_json,
                    prev_hash, entry_hash, entry_id,
                ),
            )
            self._conn.commit()

        return AuditEntry(
            seq=seq, ts=ts, actor=actor, action=action, target=target,
            verdict=verdict, params=params, prev_hash=prev_hash,
            entry_hash=entry_hash, entry_id=entry_id,
        )

    # ────────────────────────────────────────────────────────────────

    def verify_chain(self) -> IntegrityReport:
        """Walk the chain from genesis; report the first broken link."""
        rows = self._conn.execute(
            "SELECT * FROM audit_entries ORDER BY seq ASC"
        ).fetchall()

        prev_hash = GENESIS_HASH
        for row in rows:
            expected = _compute_hash(
                seq=row["seq"], ts=row["ts"], actor=row["actor"],
                action=row["action"], target=row["target"],
                verdict=row["verdict"], params_json=row["params_json"],
                prev_hash=prev_hash,
            )
            if row["prev_hash"] != prev_hash:
                return IntegrityReport(
                    ok=False, total_entries=len(rows),
                    first_bad_seq=row["seq"],
                    expected_hash=prev_hash, actual_hash=row["prev_hash"],
                    notes=[
                        f"row {row['seq']}: prev_hash mismatch — "
                        "chain broken (someone deleted or reordered rows)"
                    ],
                )
            if row["entry_hash"] != expected:
                return IntegrityReport(
                    ok=False, total_entries=len(rows),
                    first_bad_seq=row["seq"],
                    expected_hash=expected, actual_hash=row["entry_hash"],
                    notes=[
                        f"row {row['seq']}: entry_hash mismatch — "
                        "row content was modified after recording"
                    ],
                )
            prev_hash = row["entry_hash"]

        return IntegrityReport(ok=True, total_entries=len(rows))

    # ────────────────────────────────────────────────────────────────
    # Read API
    # ────────────────────────────────────────────────────────────────

    def head(self) -> str:
        """Return the latest entry_hash, or GENESIS_HASH if empty."""
        return self._latest_hash()

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM audit_entries").fetchone()
        return int(row[0])

    def __iter__(self) -> Iterable[AuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM audit_entries ORDER BY seq ASC"
        ).fetchall()
        return iter(_row_to_entry(r) for r in rows)

    def last(self, n: int = 50) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM audit_entries ORDER BY seq DESC LIMIT ?",
            (int(n),),
        ).fetchall()
        return [_row_to_entry(r) for r in reversed(rows)]

    def get(self, seq: int) -> AuditEntry | None:
        row = self._conn.execute(
            "SELECT * FROM audit_entries WHERE seq = ?", (int(seq),),
        ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_a) -> None:
        self.close()

    # ────────────────────────────────────────────────────────────────
    # Private
    # ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_entries (
                    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    target      TEXT NOT NULL,
                    verdict     TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    prev_hash   TEXT NOT NULL,
                    entry_hash  TEXT NOT NULL UNIQUE,
                    entry_id    TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_entries(action)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_entries(target)"
            )
            self._conn.commit()

    def _latest_hash(self) -> str:
        row = self._conn.execute(
            "SELECT entry_hash FROM audit_entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else GENESIS_HASH

    def _next_seq(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(seq) AS m FROM audit_entries"
        ).fetchone()
        current = row["m"] if row and row["m"] is not None else 0
        return int(current) + 1


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _compute_hash(
    *,
    seq: int,
    ts: str,
    actor: str,
    action: str,
    target: str,
    verdict: str,
    params_json: str,
    prev_hash: str,
) -> str:
    blob = "|".join((
        str(seq), ts, actor, action, target, verdict,
        params_json, prev_hash,
    )).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        seq=row["seq"], ts=row["ts"], actor=row["actor"], action=row["action"],
        target=row["target"], verdict=row["verdict"],
        params=json.loads(row["params_json"]),
        prev_hash=row["prev_hash"], entry_hash=row["entry_hash"],
        entry_id=row["entry_id"],
    )
