"""Pytest fixtures for Project Intelligence DB tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure package root (parent of project_intelligence) is on path
ROOT = Path(__file__).resolve().parents[1].parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project_intelligence.db.connection import connect  # noqa: E402
from project_intelligence.db.migrate import apply_migrations  # noqa: E402


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_intelligence.db"
    apply_migrations(path)
    return path


@pytest.fixture
def conn(db_path: Path):
    c = connect(db_path)
    yield c
    c.close()