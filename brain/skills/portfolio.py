"""
brain/skills/portfolio.py — Per-profession track record + self-rating.

The Portfolio is the agent's *episodic memory of its own work*. After
every Job completes (success or failure), the orchestrator drops a
`PortfolioEntry` here. The store gives:

    - per-profession win/loss/dollars accounting,
    - rolling self-rating that the SkillRegistry can consult when
      multiple professions match a brief (prefer the one with the
      stronger track record),
    - a queryable history for the operator (last N jobs per
      profession, total tokens spent, average delivery time).

Storage: a small SQLite table. No vector embeddings — the Portfolio is
*structured* memory, separate from `SemanticMemory`.

Self-rating formula
───────────────────
    rating = (success_count + 1) / (total_count + 2)         # Laplace smoothing
    confidence = 1 - 1 / sqrt(total_count + 1)               # 0..1, asymptotic

Both stay in `[0, 1]`. A profession with no history starts at 0.5 / 0.0
which means "neutral guess with no confidence" — exactly what we want.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Records
# ════════════════════════════════════════════════════════════════════

@dataclass
class PortfolioEntry:
    """One row in the Portfolio."""

    id:            str
    profession_id: str
    job_id:        str
    success:       bool
    delivered_at:  str       # ISO 8601 UTC
    tokens_used:   int = 0
    dollars_spent: float = 0.0
    duration_sec:  float = 0.0
    deliverables:  list[str] = field(default_factory=list)
    verifier_summary: str = ""
    notes:         list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "id":               self.id,
            "profession_id":    self.profession_id,
            "job_id":           self.job_id,
            "success":          1 if self.success else 0,
            "delivered_at":     self.delivered_at,
            "tokens_used":      int(self.tokens_used),
            "dollars_spent":    float(self.dollars_spent),
            "duration_sec":     float(self.duration_sec),
            "deliverables":     json.dumps(self.deliverables),
            "verifier_summary": self.verifier_summary,
            "notes":            json.dumps(self.notes),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PortfolioEntry":
        return cls(
            id=row["id"],
            profession_id=row["profession_id"],
            job_id=row["job_id"],
            success=bool(row["success"]),
            delivered_at=row["delivered_at"],
            tokens_used=int(row["tokens_used"]),
            dollars_spent=float(row["dollars_spent"]),
            duration_sec=float(row["duration_sec"]),
            deliverables=json.loads(row["deliverables"] or "[]"),
            verifier_summary=row["verifier_summary"] or "",
            notes=json.loads(row["notes"] or "[]"),
        )


@dataclass(frozen=True)
class ProfessionStats:
    profession_id:   str
    total:           int
    successes:       int
    failures:        int
    rating:          float       # 0..1, Laplace-smoothed success rate
    confidence:      float       # 0..1, grows with sample size
    tokens_total:    int
    dollars_total:   float
    last_delivered:  str | None

    def to_dict(self) -> dict:
        return {
            "profession_id":  self.profession_id,
            "total":          self.total,
            "successes":      self.successes,
            "failures":       self.failures,
            "rating":         round(self.rating, 4),
            "confidence":     round(self.confidence, 4),
            "tokens_total":   self.tokens_total,
            "dollars_total":  round(self.dollars_total, 4),
            "last_delivered": self.last_delivered,
        }


# ════════════════════════════════════════════════════════════════════
# Store
# ════════════════════════════════════════════════════════════════════

class Portfolio:
    """Append-mostly track record per profession.

    Usage:
        portfolio = Portfolio(Path("data/portfolio.db"))
        portfolio.record(PortfolioEntry(
            id="auto", profession_id="text_editor", job_id="j1",
            success=True, delivered_at=datetime.utcnow().isoformat(),
            tokens_used=950, dollars_spent=0.012,
        ))
        stats = portfolio.stats("text_editor")
        ranked = portfolio.rank_professions(["text_editor", "translator_en_ru"])
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio (
                id               TEXT PRIMARY KEY,
                profession_id    TEXT NOT NULL,
                job_id           TEXT NOT NULL,
                success          INTEGER NOT NULL,
                delivered_at     TEXT NOT NULL,
                tokens_used      INTEGER NOT NULL DEFAULT 0,
                dollars_spent    REAL    NOT NULL DEFAULT 0,
                duration_sec     REAL    NOT NULL DEFAULT 0,
                deliverables     TEXT    NOT NULL DEFAULT '[]',
                verifier_summary TEXT    NOT NULL DEFAULT '',
                notes            TEXT    NOT NULL DEFAULT '[]'
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_portfolio_prof ON portfolio(profession_id)"
        )
        self._conn.commit()

    # ────────────────────────────────────────────────────────────────
    # Write
    # ────────────────────────────────────────────────────────────────

    def record(self, entry: PortfolioEntry) -> PortfolioEntry:
        if entry.id == "auto" or not entry.id:
            entry = PortfolioEntry(
                id=uuid.uuid4().hex[:16],
                profession_id=entry.profession_id,
                job_id=entry.job_id,
                success=entry.success,
                delivered_at=entry.delivered_at,
                tokens_used=entry.tokens_used,
                dollars_spent=entry.dollars_spent,
                duration_sec=entry.duration_sec,
                deliverables=entry.deliverables,
                verifier_summary=entry.verifier_summary,
                notes=entry.notes,
            )
        row = entry.to_row()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO portfolio
                (id, profession_id, job_id, success, delivered_at,
                 tokens_used, dollars_spent, duration_sec,
                 deliverables, verifier_summary, notes)
            VALUES
                (:id, :profession_id, :job_id, :success, :delivered_at,
                 :tokens_used, :dollars_spent, :duration_sec,
                 :deliverables, :verifier_summary, :notes)
            """,
            row,
        )
        self._conn.commit()
        return entry

    # ────────────────────────────────────────────────────────────────
    # Read
    # ────────────────────────────────────────────────────────────────

    def stats(self, profession_id: str) -> ProfessionStats:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(success) AS successes,
                SUM(tokens_used) AS tokens_total,
                SUM(dollars_spent) AS dollars_total,
                MAX(delivered_at) AS last_delivered
            FROM portfolio
            WHERE profession_id = ?
            """,
            (profession_id,),
        ).fetchone()
        total = int(row["total"] or 0)
        successes = int(row["successes"] or 0)
        failures = total - successes
        return ProfessionStats(
            profession_id=profession_id,
            total=total,
            successes=successes,
            failures=failures,
            rating=_smoothed_rating(successes, total),
            confidence=_confidence(total),
            tokens_total=int(row["tokens_total"] or 0),
            dollars_total=float(row["dollars_total"] or 0.0),
            last_delivered=row["last_delivered"],
        )

    def all_stats(self) -> list[ProfessionStats]:
        rows = self._conn.execute(
            "SELECT DISTINCT profession_id FROM portfolio"
        ).fetchall()
        return [self.stats(r["profession_id"]) for r in rows]

    def history(self, profession_id: str, *, limit: int = 50) -> list[PortfolioEntry]:
        rows = self._conn.execute(
            "SELECT * FROM portfolio WHERE profession_id = ? "
            "ORDER BY delivered_at DESC LIMIT ?",
            (profession_id, int(limit)),
        ).fetchall()
        return [PortfolioEntry.from_row(r) for r in rows]

    def rank_professions(self, candidates: list[str]) -> list[ProfessionStats]:
        """Sort the given profession_ids by `(rating × confidence)` desc.

        Untested professions (confidence 0) fall to the bottom. Use this
        as a tie-breaker in SkillRegistry.match — when several professions
        could serve the brief, prefer the one with proven success.
        """
        stats = [self.stats(p) for p in candidates]
        stats.sort(key=lambda s: s.rating * s.confidence, reverse=True)
        return stats

    # ────────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Portfolio":
        return self

    def __exit__(self, *_a) -> None:
        self.close()


# ════════════════════════════════════════════════════════════════════
# Math
# ════════════════════════════════════════════════════════════════════

def _smoothed_rating(successes: int, total: int) -> float:
    return (successes + 1) / (total + 2)


def _confidence(total: int) -> float:
    if total <= 0:
        return 0.0
    return 1.0 - 1.0 / math.sqrt(total + 1)
