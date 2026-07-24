"""Atomic migration transaction: SQL + schema_migrations or full rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_intelligence.db.connection import connect, get_schema_version
from project_intelligence.db.migrate import (
    apply_migrations,
    apply_one_migration,
    applied_versions,
    ensure_migrations_table,
)


def test_mid_migration_failure_rolls_back(tmp_path, monkeypatch):
    """If a statement fails mid-migration, neither schema objects nor
    schema_migrations row should remain from that version."""
    db = tmp_path / "atomic.db"
    # Apply real migrations first so baseline is clean
    apply_migrations(db)

    conn = connect(db)
    ensure_migrations_table(conn)
    conn.commit()
    before = applied_versions(conn)
    assert "001" in before

    # Fake a broken migration 002
    broken = tmp_path / "002_broken.sql"
    broken.write_text(
        """
        CREATE TABLE should_not_exist (id TEXT PRIMARY KEY);
        INSERT INTO should_not_exist (id) VALUES ('ok');
        -- force failure
        INSERT INTO should_not_exist (id) VALUES ('ok');
        """
    )

    with pytest.raises(Exception):
        apply_one_migration(conn, broken, "002")

    after = applied_versions(conn)
    assert "002" not in after
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'should_not_exist'"
    ).fetchone()
    assert row is None
    assert get_schema_version(conn) == "1"
    conn.close()


def test_successful_migration_records_version(tmp_path):
    db = tmp_path / "ok.db"
    applied = apply_migrations(db)
    assert "001" in applied
    conn = connect(db)
    assert "001" in applied_versions(conn)
    assert get_schema_version(conn) == "1"
    conn.close()
