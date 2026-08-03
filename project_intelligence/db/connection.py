"""SQLite connection helpers. Always enables foreign_keys."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def connect(db_path: PathLike, *, readonly: bool = False) -> sqlite3.Connection:
    """Open SQLite connection with foreign_keys=ON and row_factory."""
    path = Path(db_path)
    if readonly:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Verify
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    if row is None or int(row[0]) != 1:
        conn.close()
        raise RuntimeError("PRAGMA foreign_keys could not be enabled")
    return conn


def get_schema_version(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["value"] if row else None