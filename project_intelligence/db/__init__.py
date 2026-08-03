"""Database access and migrations for Project Intelligence."""

from project_intelligence.db.connection import connect, get_schema_version
from project_intelligence.db.migrate import apply_migrations, migration_files

__all__ = [
    "connect",
    "get_schema_version",
    "apply_migrations",
    "migration_files",
]