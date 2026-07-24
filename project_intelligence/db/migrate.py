"""Apply numbered SQL migrations atomically (SQL + schema_migrations in one txn)."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from project_intelligence.db.connection import connect, get_schema_version

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIG_RE = re.compile(r"^(\d+)_.*\.sql$")


def migration_files() -> list[Path]:
    files = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        if p.is_file() and _MIG_RE.match(p.name):
            files.append(p)
    return files


def _split_sql(script: str) -> list[str]:
    """Split SQL script into statements.

    Handles line comments, single-quoted strings, and CREATE TRIGGER
    BEGIN...END blocks (semicolons inside triggers are not terminators).
    """
    lines: list[str] = []
    for line in script.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    begin_depth = 0
    i = 0
    upper = text.upper()

    def at_keyword(pos: int, word: str) -> bool:
        end = pos + len(word)
        if upper[pos:end] != word:
            return False
        if pos > 0 and (text[pos - 1].isalnum() or text[pos - 1] == "_"):
            return False
        if end < len(text) and (text[end].isalnum() or text[end] == "_"):
            return False
        return True

    while i < len(text):
        ch = text[i]
        if ch == "'" and not in_single:
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'" and in_single:
            if i + 1 < len(text) and text[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_single = False
            buf.append(ch)
            i += 1
            continue
        if not in_single:
            if at_keyword(i, "BEGIN"):
                begin_depth += 1
                buf.append(text[i : i + 5])
                i += 5
                continue
            if at_keyword(i, "END") and begin_depth > 0:
                begin_depth -= 1
                buf.append(text[i : i + 3])
                i += 3
                continue
            if ch == ";" and begin_depth == 0:
                stmt = "".join(buf).strip()
                if stmt:
                    parts.append(stmt)
                buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def apply_one_migration(conn: sqlite3.Connection, path: Path, version: str) -> None:
    """Apply one migration file and record it in a single explicit transaction."""
    sql = path.read_text(encoding="utf-8")
    statements = _split_sql(sql)
    old_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.isolation_level = old_isolation


def apply_migrations(db_path: str | Path) -> list[str]:
    """Apply pending migrations. Returns list of applied version ids."""
    conn = connect(db_path)
    applied: list[str] = []
    try:
        ensure_migrations_table(conn)
        conn.commit()
        done = applied_versions(conn)
        for path in migration_files():
            m = _MIG_RE.match(path.name)
            assert m
            version = m.group(1)
            if version in done:
                continue
            apply_one_migration(conn, path, version)
            applied.append(version)
        return applied
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="project_intelligence migrate")
    parser.add_argument(
        "--db",
        default="data/intelligence.db",
        help="Path to SQLite database",
    )
    args = parser.parse_args(argv)
    applied = apply_migrations(args.db)
    ver = get_schema_version(connect(args.db))
    if applied:
        print(f"Applied migrations: {', '.join(applied)}; schema_version={ver}")
    else:
        print(f"No pending migrations; schema_version={ver}")


if __name__ == "__main__":
    main()
