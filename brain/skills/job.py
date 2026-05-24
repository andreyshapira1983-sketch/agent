"""
brain/skills/job.py — Job + JobStore.

A Job is the agent's representation of one client request. The Brain
never sees raw emails / Telegram messages — they're parsed into Job
objects first, and persisted in SQLite so crashes don't lose money.

State machine (transitions only — Brain controls when):

    received ──► matched ──► in_progress ──► delivered
        │           │             │              │
        │           │             ▼              ▼
        │           └─► declined  failed       paid

`declined` covers "no matching profession" and "missing capabilities".
`failed` covers "tried and gave up / verifier blocked".

JobStore is intentionally tiny: SQLite, no ORM, JSON columns for the
mutable parts. Migrations happen by creating new tables — we never
ALTER existing ones in place.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Status enum
# ────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    """Lifecycle of one Job. String-valued so it round-trips through JSON cleanly."""

    RECEIVED    = "received"
    MATCHED     = "matched"
    DECLINED    = "declined"
    IN_PROGRESS = "in_progress"
    DELIVERED   = "delivered"
    FAILED      = "failed"
    PAID        = "paid"

    @classmethod
    def terminal(cls) -> set["JobStatus"]:
        return {cls.DECLINED, cls.DELIVERED, cls.FAILED, cls.PAID}

    def is_terminal(self) -> bool:
        return self in self.terminal()


# Allowed transitions — kept conservative to prevent bugs from corrupting state
_ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.RECEIVED:    {JobStatus.MATCHED, JobStatus.DECLINED, JobStatus.FAILED},
    JobStatus.MATCHED:     {JobStatus.IN_PROGRESS, JobStatus.DECLINED, JobStatus.FAILED},
    JobStatus.IN_PROGRESS: {JobStatus.DELIVERED, JobStatus.FAILED},
    JobStatus.DELIVERED:   {JobStatus.PAID, JobStatus.FAILED},
    JobStatus.DECLINED:    set(),
    JobStatus.FAILED:      set(),
    JobStatus.PAID:        set(),
}


class JobTransitionError(ValueError):
    """Raised when a JobStore.update_status call requests an illegal transition."""


# ────────────────────────────────────────────────────────────────────
# Job dataclass
# ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Job:
    """One freelance work item.

    All file paths are stored as strings — the consumer is responsible for
    Path-handling. We store *absolute* paths to avoid breaking when the
    process changes cwd.
    """

    brief: str
    source: str                                    # "email" | "telegram" | "cli" | ...
    client_id: str                                 # email address / chat_id / ...
    id: str = field(default_factory=_new_id)
    attachments: list[str] = field(default_factory=list)
    deadline: datetime | None = None
    price_offered_usd: float | None = None
    status: JobStatus = JobStatus.RECEIVED
    profession_id: str | None = None
    deliverables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # human-readable trail
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    # ──────────────────────────────────────────────────────────────────

    def add_note(self, note: str) -> None:
        self.notes.append(f"[{_now().isoformat()}] {note}")
        self.updated_at = _now()

    def add_deliverable(self, path: str | Path) -> None:
        self.deliverables.append(str(Path(path).resolve()))
        self.updated_at = _now()

    def is_active(self) -> bool:
        return not self.status.is_terminal()

    def to_row(self) -> dict[str, Any]:
        """Flat representation for SQLite. List/optional fields go through JSON."""
        return {
            "id":                self.id,
            "brief":             self.brief,
            "source":            self.source,
            "client_id":         self.client_id,
            "attachments":       json.dumps(self.attachments),
            "deadline":          self.deadline.isoformat() if self.deadline else None,
            "price_offered_usd": self.price_offered_usd,
            "status":            self.status.value,
            "profession_id":     self.profession_id,
            "deliverables":      json.dumps(self.deliverables),
            "notes":             json.dumps(self.notes),
            "created_at":        self.created_at.isoformat(),
            "updated_at":        self.updated_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        return cls(
            id=row["id"],
            brief=row["brief"],
            source=row["source"],
            client_id=row["client_id"],
            attachments=json.loads(row["attachments"] or "[]"),
            deadline=datetime.fromisoformat(row["deadline"]) if row.get("deadline") else None,
            price_offered_usd=row.get("price_offered_usd"),
            status=JobStatus(row["status"]),
            profession_id=row.get("profession_id"),
            deliverables=json.loads(row["deliverables"] or "[]"),
            notes=json.loads(row["notes"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


# ────────────────────────────────────────────────────────────────────
# JobStore
# ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    brief             TEXT NOT NULL,
    source            TEXT NOT NULL,
    client_id         TEXT NOT NULL,
    attachments       TEXT NOT NULL DEFAULT '[]',
    deadline          TEXT,
    price_offered_usd REAL,
    status            TEXT NOT NULL,
    profession_id     TEXT,
    deliverables      TEXT NOT NULL DEFAULT '[]',
    notes             TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_client     ON jobs(client_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
"""


class JobStore:
    """SQLite-backed persistence for Job objects.

    Tiny by design — query patterns we actually need:
        create(job)
        get(job_id) -> Job
        update_status(job_id, new_status, note?)
        set_profession(job_id, profession_id)
        save(job)            # general 'flush back' for accumulated mutations
        list_active()        # all non-terminal jobs
        list_by_client(cid)  # history for one client
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """
        Pass `db_path=":memory:"` for tests. Default is a `jobs.db` file in
        a `data/` directory next to the cwd.
        """
        if db_path is None:
            db_path = Path("data") / "jobs.db"
        self._path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path))

        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ──────────────────────────────────────────────────────────────────

    def create(self, job: Job) -> Job:
        """Insert a new Job. Raises sqlite3.IntegrityError if id already exists."""
        row = job.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        self._conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", row)
        self._conn.commit()
        logger.info("[JobStore] created job %s (%s)", job.id, job.source)
        return job

    def get(self, job_id: str) -> Job:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Job not found: {job_id}")
        return Job.from_row(dict(row))

    def get_optional(self, job_id: str) -> Job | None:
        try:
            return self.get(job_id)
        except KeyError:
            return None

    def save(self, job: Job) -> None:
        """Flush every mutable field back to disk. Updates updated_at."""
        job.updated_at = _now()
        row = job.to_row()
        self._conn.execute(
            """
            UPDATE jobs SET
                brief = :brief,
                attachments = :attachments,
                deadline = :deadline,
                price_offered_usd = :price_offered_usd,
                status = :status,
                profession_id = :profession_id,
                deliverables = :deliverables,
                notes = :notes,
                updated_at = :updated_at
            WHERE id = :id
            """,
            row,
        )
        self._conn.commit()

    def update_status(
        self,
        job_id: str,
        new_status: JobStatus,
        note: str | None = None,
    ) -> Job:
        job = self.get(job_id)
        if new_status == job.status:
            return job
        allowed = _ALLOWED_TRANSITIONS.get(job.status, set())
        if new_status not in allowed:
            raise JobTransitionError(
                f"Illegal transition for job {job_id}: "
                f"{job.status.value} → {new_status.value}. "
                f"Allowed: {sorted(s.value for s in allowed)}"
            )
        job.status = new_status
        if note:
            job.add_note(f"status → {new_status.value}: {note}")
        else:
            job.add_note(f"status → {new_status.value}")
        self.save(job)
        return job

    def set_profession(self, job_id: str, profession_id: str) -> Job:
        job = self.get(job_id)
        job.profession_id = profession_id
        job.add_note(f"matched profession: {profession_id}")
        self.save(job)
        return job

    def list_active(self) -> list[Job]:
        terminal = [s.value for s in JobStatus.terminal()]
        placeholders = ",".join(["?"] * len(terminal))
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE status NOT IN ({placeholders}) ORDER BY created_at",
            terminal,
        ).fetchall()
        return [Job.from_row(dict(r)) for r in rows]

    def list_by_client(self, client_id: str) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE client_id = ? ORDER BY created_at",
            (client_id,),
        ).fetchall()
        return [Job.from_row(dict(r)) for r in rows]

    def list_by_status(self, status: JobStatus) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at",
            (status.value,),
        ).fetchall()
        return [Job.from_row(dict(r)) for r in rows]

    def __iter__(self) -> Iterator[Job]:
        rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return iter(Job.from_row(dict(r)) for r in rows)

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
