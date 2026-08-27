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


def test_a_file_that_merely_lies_in_the_workspace_is_not(repo: Path) -> None:
    """Граница, ради которой всё и затевалось.

    Загруженная извне страница ложится в рабочую папку, но в историю не
    попадает. Признать её своей значило бы восстановить ошибку 2026-08-14.
    Изменить же отслеживаемый файл агент автономно не может: перезапись
    необратима и эскалируется (инвариант I-1), — на этом признак и держится.
    """
    (repo / "docs" / "ingested.md").write_text("чужое\n", encoding="utf-8")

    assert is_committed_source("file:docs/ingested.md", repo) is False


def test_a_network_source_is_never_ours(repo: Path) -> None:
    """Не файл — не своё, какой бы ни была метка."""
    assert is_committed_source("web:https://example.invalid/page", repo) is False
    assert is_committed_source("step:s1", repo) is False
    assert is_committed_source("", repo) is False


def test_a_path_escaping_the_repository_is_never_ours(repo: Path) -> None:
    """Обход через `..` не должен давать пропуск."""
    assert is_committed_source("file:../outside.md", repo) is False


def test_the_blocked_branch_consults_provenance(repo: Path) -> None:
    """Проводка: решение обязано стоять в горячем пути, а не только в модуле.

    Сквозное подтверждение придёт из журнала ближайшего планового тика —
    событие `injection_blocked_downgraded` вместо голой блокировки. До тех пор
    это закрепление источника: без него правка тихо осталась бы неподключённой,
    как уже случалось (MIR-138).
    """
    import inspect

    from core import loop_step_execution as mod

    # Смотрим МОДУЛЬ, а не одну функцию: первая версия пина держалась за имя
    # `_execute_step` и покраснела, как только обработку блокировки вынесли в
    # помощника. Пин обязан переживать перестановку, иначе он охраняет форму,
    # а не свойство.
    src = inspect.getsource(mod)

    assert "is_committed_source" in src
    assert "injection_blocked_downgraded" in src
    assert "_blocked_output_or_replan" in inspect.getsource(
        mod.AgentLoopStepExecution._execute_step
    ), "решение должно вызываться из горячего пути, а не просто жить в модуле"
    # Первая версия этой проверки смотрела ТОЛЬКО текст исходника и прошла,
    # когда имя стояло в коде, а импорта не было: в бою это дало бы NameError
    # на первом же заблокированном чтении. Свидетель обязан требовать
    # СВЯЗАННОГО имени, а не совпадения строки.
    assert callable(getattr(mod, "is_committed_source", None))
