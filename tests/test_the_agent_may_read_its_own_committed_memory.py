"""Свои зафиксированные документы агент читает — помеченными, а не отнятыми.

Замер, отвергнутые варианты и границы: раздел 6 в
docs/audit/PROSPECTIVE_AUTONOMY_HAZARD_AUDIT.md.

Ключевая деталь: «своё» определяется ПРОИСХОЖДЕНИЕМ (файл под版本ным
контролем), а не расположением. Расположение как признак доверия этот проект
уже опроверг 2026-08-14, когда `file_read` убрали из списка исключений.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.repo_provenance import is_committed_source


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)  # noqa: S607  # nosec B603 B607
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("совет\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)  # noqa: S607  # nosec B603 B607
    subprocess.run(  # nosec B603 B607
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],  # noqa: S607
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def test_a_committed_document_is_our_own(repo: Path) -> None:
    """Красный свидетель: реестр дефектов — свой, и это проверяемо.

    Живьём 2026-08-27: `docs/audit/MASTER_ISSUE_REGISTRY.md` даёт 263 находки и
    вердикт `blocked`, `docs/CODE_NOTES.md` — 126. Оба заблокированы, оба
    зафиксированы в истории.
    """
    assert is_committed_source("file:docs/notes.md", repo) is True


def test_a_path_escaping_the_repository_is_never_ours(repo: Path) -> None:
    """Обход через `..` не должен давать пропуск."""
    assert is_committed_source("file:../outside.md", repo) is False
