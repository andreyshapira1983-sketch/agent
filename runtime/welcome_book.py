"""
runtime/welcome_book.py — Track whom the agent has greeted.

A two-column SQLite table: `client_id` (text PK) and `first_seen_at`
(ISO timestamp). Used by `ChatHandler` to prepend a one-time welcome
to the very first reply a client gets — only once per client_id, even
across process restarts.

Intentionally lightweight: no migrations, no schema versioning. If you
ever want to "forget" a client (so they get the welcome again on their
next message), just delete their row.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class WelcomeBook:
    """Persistent set of client ids that have already been greeted."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS welcome (
                    client_id     TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    # ────────────────────────────────────────────────────────────────

    def has_greeted(self, client_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM welcome WHERE client_id = ?", (client_id,),
            ).fetchone()
            return row is not None

    def mark_greeted(self, client_id: str) -> bool:
        """Insert the row if missing. Returns True if this call did it."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO welcome (client_id, first_seen_at) VALUES (?, ?)",
                (client_id, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def forget(self, client_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM welcome WHERE client_id = ?", (client_id,),
            )
            self._conn.commit()

    def __len__(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM welcome").fetchone()
            return int(row[0])

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "WelcomeBook":
        return self

    def __exit__(self, *_a) -> None:
        self.close()
