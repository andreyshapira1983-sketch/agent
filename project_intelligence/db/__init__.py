"""Database access and migrations for Project Intelligence."""

from project_intelligence.db.connection import connect, get_schema_version
from project_intelligence.db.migrate import apply_migrations, migration_files

__all__ = [
    "apply_migrations",
    "connect",
    "get_schema_version",
    "migration_files",
]