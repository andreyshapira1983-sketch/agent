"""
brain/memory/procedural_memory.py — Procedural Memory (S3.4)

Stores learned skills and successful action trajectories.
Used by the Brain to reuse proven patterns and by S7.2 Skill Distiller
to build fine-tuning datasets.

Two sub-modules (in one class):

    S3.4.1 Skill Library:
        - register_skill()    — add a named, reusable skill
        - match_skill()       — semantic search over skill descriptions
        - update_skill_stats() — track success/failure per skill
        - list_skills()       — filtered listing

    S3.4.2 Trajectory Store:
        - save_trajectory()   — record a complete input→steps→outcome trace
        - get_trajectories()  — query by skill / outcome / session
        - build_few_shots()   — extract top-scored examples for prompting
        - analyze_failures()  — summarise failure patterns per skill

Backend:
    SQLite  — structured storage, survives restarts
    ChromaDB — semantic index on skill descriptions for match_skill()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH    = "data/procedural.db"
DEFAULT_CHROMA_DIR = "data/procedural_chroma"


class ProceduralMemory:
    """
    Long-term store for learned skills and action trajectories.

    Skill Library — named reusable patterns the agent can look up by
    semantic similarity (ChromaDB) or exact name.

    Trajectory Store — records of completed tasks: what was asked,
    what steps were taken, what the outcome was, and a quality score.
    The Brain tags trajectories with outcomes; S7.2 Skill Distiller
    harvests the top-scored ones to build fine-tuning datasets.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        chroma_dir: str = DEFAULT_CHROMA_DIR,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        self._chroma_dir = Path(chroma_dir)
        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._collection: Any = self._init_chroma()
        self._use_chroma: bool = self._collection is not None

        logger.info(
            "[ProceduralMemory] Ready | db=%s vector=%s",
            self._db_path,
            "chromadb" if self._use_chroma else "none",
        )

    # ══════════════════════════════════════════════════════════════════
    # S3.4.1 Skill Library
    # ══════════════════════════════════════════════════════════════════

    def register_skill(
        self,
        name: str,
        description: str,
        *,
        template: str | list | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """
        Register a named reusable skill.

        Args:
            name:        unique skill identifier (e.g. "sort_python_list")
            description: human-readable description (used for semantic search)
            template:    optional step-by-step pattern (str or list of steps)
            tags:        optional category tags (e.g. ["python", "sorting"])

        Returns:
            SQLite row id.
        """
        now = _now()
        template_json = json.dumps(template) if template is not None else None
        tags_str = ",".join(tags) if tags else ""

        with self._conn() as conn:
            # Upsert — update if name already exists
            existing = conn.execute(
                "SELECT id FROM skills WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE skills SET description=?, template=?, tags=?, updated_at=?
                       WHERE name=?""",
                    (description, template_json, tags_str, now, name),
                )
                skill_id: int = existing[0]
                logger.info("[ProceduralMemory] Updated skill '%s' id=%d", name, skill_id)
            else:
                cursor = conn.execute(
                    """INSERT INTO skills (name, description, template, tags,
                                          usage_count, success_count, created_at, updated_at)
                       VALUES (?,?,?,?,0,0,?,?)""",
                    (name, description, template_json, tags_str, now, now),
                )
                skill_id = cursor.lastrowid  # type: ignore[assignment]
                logger.info("[ProceduralMemory] Registered skill '%s' id=%d", name, skill_id)

        # Index description in ChromaDB for semantic search
        self._upsert_chroma(f"skill_{skill_id}", description, {"name": name, "tags": tags_str})
        return skill_id

    def get_skill(self, name: str) -> dict | None:
        """Return skill record by exact name, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id,name,description,template,tags,usage_count,success_count,"
                "created_at,updated_at FROM skills WHERE name=?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return self._skill_row_to_dict(row)

    def list_skills(self, tag: str | None = None) -> list[dict]:
        """
        List all skills, optionally filtered by tag.
        Returns list sorted by success_count DESC.
        """
        with self._conn() as conn:
            if tag:
                rows = conn.execute(
                    "SELECT id,name,description,template,tags,usage_count,success_count,"
                    "created_at,updated_at FROM skills WHERE tags LIKE ?"
                    " ORDER BY success_count DESC",
                    (f"%{tag}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,name,description,template,tags,usage_count,success_count,"
                    "created_at,updated_at FROM skills ORDER BY success_count DESC"
                ).fetchall()
        return [self._skill_row_to_dict(r) for r in rows]

    def match_skill(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Find skills semantically similar to the query.
        Returns list of {name, description, score, success_rate}.
        Falls back to substring match if ChromaDB unavailable.
        """
        if not query.strip():
            return []

        if self._use_chroma:
            try:
                count = self._collection.count()
                if count == 0:
                    return []
                results = self._collection.query(
                    query_texts=[query],
                    n_results=min(top_k, count),
                )
                docs  = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0] or [{}] * len(docs)
                dists = results.get("distances", [[]])[0]
                out = []
                for doc, meta, dist in zip(docs, metas, dists):
                    skill = self.get_skill(meta.get("name", ""))
                    if skill:
                        skill["score"] = round(1.0 - dist, 4)
                        out.append(skill)
                return out
            except Exception as exc:
                logger.warning("[ProceduralMemory] match_skill chroma error: %s", exc)

        # Fallback: keyword match
        q_words = set(query.lower().split())
        skills = self.list_skills()
        scored = []
        for s in skills:
            words = set((s["description"] + " " + s["name"]).lower().split())
            score = len(q_words & words) / max(len(q_words), 1)
            if score > 0:
                scored.append({**s, "score": round(score, 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def update_skill_stats(self, name: str, *, success: bool) -> None:
        """Increment usage_count and optionally success_count for a skill."""
        with self._conn() as conn:
            if success:
                conn.execute(
                    "UPDATE skills SET usage_count=usage_count+1, success_count=success_count+1,"
                    " updated_at=? WHERE name=?",
                    (_now(), name),
                )
            else:
                conn.execute(
                    "UPDATE skills SET usage_count=usage_count+1, updated_at=? WHERE name=?",
                    (_now(), name),
                )
        logger.debug("[ProceduralMemory] Stats updated skill='%s' success=%s", name, success)

    def delete_skill(self, name: str) -> None:
        """Remove a skill and all its trajectories."""
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()
            if row:
                conn.execute("DELETE FROM trajectories WHERE skill_name=?", (name,))
                conn.execute("DELETE FROM skills WHERE name=?", (name,))
                chroma_id = f"skill_{row[0]}"
                if self._use_chroma:
                    try:
                        self._collection.delete(ids=[chroma_id])
                    except Exception:
                        pass
                logger.info("[ProceduralMemory] Deleted skill '%s'", name)

    # ══════════════════════════════════════════════════════════════════
    # S3.4.2 Trajectory Store
    # ══════════════════════════════════════════════════════════════════

    def save_trajectory(
        self,
        skill_name: str,
        input_summary: str,
        steps: list[dict],
        outcome: str,
        *,
        session_id: str | None = None,
        score: float = 1.0,
    ) -> int:
        """
        Record a complete task trajectory.

        Args:
            skill_name:    which skill was used
            input_summary: brief description of the input / task
            steps:         list of {tool, params, result} dicts
            outcome:       "success" | "failure" | "partial"
            session_id:    optional session that generated this trajectory
            score:         quality score 0.0–1.0 (default 1.0 for explicit saves)

        Returns:
            SQLite row id.
        """
        score = max(0.0, min(1.0, float(score)))
        steps_json = json.dumps(steps, ensure_ascii=False)
        few_shot = _build_few_shot_text(input_summary, steps, outcome)

        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO trajectories
                   (skill_name, session_id, input_summary, steps, outcome, score, ts, few_shot)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    skill_name,
                    session_id or "",
                    input_summary,
                    steps_json,
                    outcome,
                    score,
                    _now(),
                    few_shot,
                ),
            )
            traj_id: int = cursor.lastrowid  # type: ignore[assignment]

        # Auto-update skill stats
        self.update_skill_stats(skill_name, success=(outcome == "success"))

        logger.info(
            "[ProceduralMemory] Trajectory saved id=%d skill='%s' outcome=%s score=%.2f",
            traj_id, skill_name, outcome, score,
        )
        return traj_id

    def get_trajectories(
        self,
        skill_name: str | None = None,
        outcome: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Query trajectories filtered by skill and/or outcome.
        Returns newest first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if skill_name:
            clauses.append("skill_name = ?")
            params.append(skill_name)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id,skill_name,session_id,input_summary,steps,outcome,score,ts,few_shot"
                f" FROM trajectories {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._traj_row_to_dict(r) for r in rows]

    def build_few_shots(self, skill_name: str, limit: int = 3) -> list[dict]:
        """
        Return the top-scored successful trajectories for a skill,
        formatted as few-shot examples for prompting.

        Returns list of {input_summary, steps, few_shot_text}.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id,skill_name,session_id,input_summary,steps,outcome,score,ts,few_shot
                   FROM trajectories
                   WHERE skill_name=? AND outcome='success'
                   ORDER BY score DESC, id DESC LIMIT ?""",
                (skill_name, limit),
            ).fetchall()
        return [self._traj_row_to_dict(r) for r in rows]

    def analyze_failures(self, skill_name: str) -> dict:
        """
        Summarise failure patterns for a skill.
        Returns {count, examples: list[input_summary], common_steps: list}.
        Used by S7.2 to improve skill templates.
        """
        failures = self.get_trajectories(skill_name=skill_name, outcome="failure", limit=50)
        if not failures:
            return {"count": 0, "examples": [], "common_steps": []}

        # Find most common step tool names across failures
        tool_freq: dict[str, int] = {}
        for t in failures:
            for step in (t.get("steps") or []):
                tool = step.get("tool", "")
                if tool:
                    tool_freq[tool] = tool_freq.get(tool, 0) + 1
        common = sorted(tool_freq, key=tool_freq.get, reverse=True)[:5]  # type: ignore[arg-type]

        return {
            "count": len(failures),
            "examples": [f["input_summary"] for f in failures[:5]],
            "common_steps": common,
        }

    def skill_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]

    def trajectory_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]

    # ══════════════════════════════════════════════════════════════════
    # Private helpers
    # ══════════════════════════════════════════════════════════════════

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT UNIQUE NOT NULL,
                    description   TEXT NOT NULL,
                    template      TEXT,
                    tags          TEXT DEFAULT '',
                    usage_count   INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name    TEXT NOT NULL,
                    session_id    TEXT DEFAULT '',
                    input_summary TEXT NOT NULL,
                    steps         TEXT DEFAULT '[]',
                    outcome       TEXT NOT NULL,
                    score         REAL DEFAULT 1.0,
                    ts            TEXT NOT NULL,
                    few_shot      TEXT DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traj_skill ON trajectories(skill_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traj_outcome ON trajectories(outcome)")

    def _init_chroma(self) -> Any:
        """Init ChromaDB collection for skill semantic search. Returns None on failure."""
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self._chroma_dir))
            collection = client.get_or_create_collection(
                name="procedural_skills",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("[ProceduralMemory] ChromaDB ready | dir=%s", self._chroma_dir)
            return collection
        except ImportError:
            logger.warning("[ProceduralMemory] chromadb not installed — keyword fallback")
            return None

    def _upsert_chroma(self, chroma_id: str, text: str, meta: dict) -> None:
        if not self._use_chroma:
            return
        try:
            existing = self._collection.get(ids=[chroma_id], include=[])
            if existing.get("ids"):
                self._collection.update(
                    ids=[chroma_id],
                    documents=[text],
                    metadatas=[meta],
                )
            else:
                self._collection.add(
                    ids=[chroma_id],
                    documents=[text],
                    metadatas=[meta],
                )
        except Exception as exc:
            logger.warning("[ProceduralMemory] ChromaDB upsert error: %s", exc)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _skill_row_to_dict(row) -> dict:
        success_count = row[6]
        usage_count   = row[5]
        success_rate  = round(success_count / usage_count, 3) if usage_count > 0 else 0.0
        template_raw  = row[3]
        return {
            "id":           row[0],
            "name":         row[1],
            "description":  row[2],
            "template":     json.loads(template_raw) if template_raw else None,
            "tags":         [t for t in (row[4] or "").split(",") if t],
            "usage_count":  usage_count,
            "success_count": success_count,
            "success_rate": success_rate,
            "created_at":   row[7],
            "updated_at":   row[8],
        }

    @staticmethod
    def _traj_row_to_dict(row) -> dict:
        steps_raw = row[4]
        return {
            "id":            row[0],
            "skill_name":    row[1],
            "session_id":    row[2],
            "input_summary": row[3],
            "steps":         json.loads(steps_raw) if steps_raw else [],
            "outcome":       row[5],
            "score":         row[6],
            "ts":            row[7],
            "few_shot":      row[8],
        }


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _build_few_shot_text(
    input_summary: str,
    steps: list[dict],
    outcome: str,
) -> str:
    """
    Format a trajectory as a human-readable few-shot example.
    Used for prompt injection and fine-tuning dataset generation.
    """
    lines = [f"Input: {input_summary}"]
    for i, step in enumerate(steps, 1):
        tool   = step.get("tool", "?")
        params = step.get("params", {})
        result = step.get("result", "")
        params_str = ", ".join(f"{k}={v!r}" for k, v in params.items()) if params else ""
        lines.append(f"Step {i}: {tool}({params_str}) → {result}")
    lines.append(f"Outcome: {outcome}")
    return "\n".join(lines)
