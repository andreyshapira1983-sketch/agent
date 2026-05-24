"""
brain/memory/episodic_memory.py — Episodic Memory (Mid-Term)

Stores history of conversations and events across sessions.
Persisted to SQLite — survives restarts.
Adds ChromaDB vector index for semantic similarity search over past episodes.

Analogy: "I remember we talked about X last Tuesday."
Brain uses this to understand patterns over time and find similar past situations.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/episodic.db"
DEFAULT_CHROMA_DIR = "data/episodic_chroma"


class EpisodicMemory:
    """
    Mid-term memory persisted to SQLite + ChromaDB vector index.

    SQLite:  structured store for chronological retrieval, session filtering, TTL.
    ChromaDB: vector index for semantic similarity search across all past episodes.

    Survives agent restarts. Can be queried by session, time range, or semantic similarity.
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
            "[EpisodicMemory] Ready | db=%s vector=%s",
            self._db_path,
            "chromadb" if self._use_chroma else "none",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, session_id: str, role: str, content: str) -> int:
        """
        Persist episode to SQLite and index in ChromaDB.
        Returns the SQLite row id of the stored episode.
        """
        ts = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO episodes (session_id, role, content, ts, outcome) VALUES (?,?,?,?,?)",
                (session_id, role, content, ts, None),
            )
            episode_id: int = cursor.lastrowid  # type: ignore[assignment]

        # Index in ChromaDB for vector similarity search
        if self._use_chroma:
            try:
                chroma_id = f"ep_{episode_id}"
                self._collection.add(
                    documents=[content],
                    metadatas=[{"session_id": session_id, "role": role, "ts": ts}],
                    ids=[chroma_id],
                )
            except Exception as exc:
                logger.warning("[EpisodicMemory] ChromaDB index failed: %s", exc)

        logger.debug(
            "[EpisodicMemory] Stored id=%d | session=%s role=%s",
            episode_id,
            session_id,
            role,
        )
        return episode_id

    def recall(self, session_id: str, limit: int = 10) -> list[dict]:
        """Return last N messages for a session, newest last."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, ts, outcome FROM episodes
                WHERE session_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        # Reverse so oldest is first
        return [
            {"id": r[0], "role": r[1], "content": r[2], "timestamp": r[3], "outcome": r[4]}
            for r in reversed(rows)
        ]

    def recall_recent(self, limit: int = 50) -> list[dict]:
        """Return last N messages across ALL sessions — for reflection."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, ts, outcome FROM episodes
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "timestamp": r[4],
                "outcome": r[5],
            }
            for r in reversed(rows)
        ]

    def recall_similar(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Semantic vector search over all stored episodes.
        Returns episodes most similar to query, sorted by relevance.
        Falls back to keyword search if ChromaDB unavailable.
        """
        if not query.strip():
            return []

        if self._use_chroma:
            return self._chroma_search(query, top_k)
        return self._keyword_search(query, top_k)

    def tag_outcome(self, episode_id: int, outcome: str) -> None:
        """
        Tag an episode with an outcome label (e.g. 'success', 'failure', 'partial').
        Brain calls this after observing the result of an action.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE episodes SET outcome = ? WHERE id = ?",
                (outcome, episode_id),
            )
        logger.debug("[EpisodicMemory] Outcome tagged id=%d outcome=%s", episode_id, outcome)

    def tag_last_outcome(self, session_id: str, outcome: str) -> None:
        """Tag the most recent episode for a session with an outcome."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row:
            self.tag_outcome(row[0], outcome)
        logger.info("[EpisodicMemory] Summary stored | session=%s", session_id)

    def store_summary(self, session_id: str, summary: str) -> int:
        """Brain can summarize a session and store it as a single episode."""
        return self.store(session_id=session_id, role="summary", content=summary)

    def forget(self, session_id: str) -> None:
        """Delete all episodes for a session from SQLite and ChromaDB."""
        # Get episode IDs before deletion for ChromaDB cleanup
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM episodes WHERE session_id = ?", (session_id,)
            ).fetchall()
            conn.execute("DELETE FROM episodes WHERE session_id = ?", (session_id,))

        if self._use_chroma and rows:
            chroma_ids = [f"ep_{r[0]}" for r in rows]
            try:
                self._collection.delete(ids=chroma_ids)
            except Exception as exc:
                logger.warning("[EpisodicMemory] ChromaDB delete failed: %s", exc)

        logger.info("[EpisodicMemory] Session deleted | session=%s", session_id)

    def forget_older_than(self, days: int) -> int:
        """Delete episodes older than N days. Returns count deleted."""
        cutoff = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from datetime import timedelta
        cutoff -= timedelta(days=days)
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM episodes WHERE ts < ?",
                (cutoff.isoformat(),),
            )
            deleted = cursor.rowcount
        logger.info("[EpisodicMemory] Pruned %d episodes older than %d days", deleted, days)
        return deleted

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    ts         TEXT NOT NULL,
                    outcome    TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON episodes(session_id)"
            )
            # Add outcome column if missing (migration for existing DBs)
            try:
                conn.execute("ALTER TABLE episodes ADD COLUMN outcome TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

    def _init_chroma(self) -> Any:
        """Initialize ChromaDB vector index. Returns None if unavailable."""
        try:
            import chromadb  # type: ignore[import]
            client = chromadb.PersistentClient(path=str(self._chroma_dir))
            collection = client.get_or_create_collection(
                name="episodic_memory",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "[EpisodicMemory] ChromaDB vector index ready | dir=%s", self._chroma_dir
            )
            return collection
        except ImportError:
            logger.warning(
                "[EpisodicMemory] chromadb not installed — vector search disabled. "
                "Install: pip install chromadb"
            )
            return None
        except Exception as exc:
            logger.warning("[EpisodicMemory] ChromaDB init failed: %s — vector search disabled", exc)
            return None

    def _chroma_search(self, query: str, top_k: int) -> list[dict]:
        count = self._collection.count()
        if count == 0:
            return []
        results = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, count),
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0] or [{}] * len(docs)
        distances = results.get("distances", [[]])[0]
        ids = results.get("ids", [[]])[0]

        output = []
        for doc, meta, dist, chroma_id in zip(docs, metas, distances, ids):
            # Parse SQLite id from chroma id "ep_<N>"
            try:
                ep_id = int(chroma_id.split("_", 1)[1])
            except (IndexError, ValueError):
                ep_id = None
            output.append(
                {
                    "id": ep_id,
                    "content": doc,
                    "score": round(1.0 - dist, 4),
                    "session_id": meta.get("session_id"),
                    "role": meta.get("role"),
                    "timestamp": meta.get("ts"),
                }
            )
        return output

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        """Keyword fallback when ChromaDB unavailable."""
        query_words = set(query.lower().split())
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, session_id, role, content, ts FROM episodes ORDER BY id DESC LIMIT 500"
            ).fetchall()
        scored = []
        for row in rows:
            words = set(row[3].lower().split())
            score = len(query_words & words) / max(len(query_words), 1)
            if score > 0:
                scored.append(
                    {
                        "id": row[0],
                        "content": row[3],
                        "score": round(score, 4),
                        "session_id": row[1],
                        "role": row[2],
                        "timestamp": row[4],
                    }
                )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
