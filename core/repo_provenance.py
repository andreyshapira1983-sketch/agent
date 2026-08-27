"""Происхождение файла: лежит он в истории репозитория или просто в папке.

Признак доверия — НЕ расположение. Расположение этот проект уже опроверг
2026-08-14, когда `file_read` убрали из списка исключений защиты от инъекций:
загрузка внешнего материала кладёт чужие страницы прямо в рабочую папку.
Зафиксированность в истории — другое: туда чужое не попадает само, а изменить
отслеживаемый файл агент автономно не может, потому что перезапись необратима и
эскалируется к человеку. Разбор: docs/audit/PROSPECTIVE_AUTONOMY_HAZARD_AUDIT.md,
раздел 6.
"""
from __future__ import annotations

import subprocess  # nosec B404 — только `git ls-files`, без оболочки
from pathlib import Path

_FILE_PREFIX = "file:"
_LS_FILES_TIMEOUT_S = 20

#: Кэш на процесс: `git ls-files` — подпроцесс, а решение принимается на каждый
#: вывод инструмента. Файл, зафиксированный ПОСЛЕ старта процесса, до конца
#: прогона считается чужим — и это безопасная сторона ошибки.
_TRACKED_CACHE: dict[str, frozenset[str]] = {}


def _tracked_paths(workspace: Path) -> frozenset[str]:
    key = str(Path(workspace).resolve())
    cached = _TRACKED_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(  # nosec B603 B607
            ["git", "ls-files", "-z"],  # noqa: S607 — git из PATH, как везде в репозитории
            cwd=key,
            capture_output=True,
            timeout=_LS_FILES_TIMEOUT_S,
            check=False,
        )
        raw = proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        # Нет git, нет репозитория, зависший вызов — «своим» не становится
        # ничто. Отказ этой проверки обязан отнимать доверие, а не выдавать его.
        raw = ""
    tracked = frozenset(p for p in raw.split("\0") if p)
    _TRACKED_CACHE[key] = tracked
    return tracked


def is_committed_source(source_label: str, workspace: Path | str) -> bool:
    """Пришёл ли этот вывод из файла, лежащего в истории репозитория.

    Метка вида ``file:<путь>``; всё остальное (сеть, шаг плана, память) — не
    своё по определению. Выход за пределы репозитория — не своё.
    """
    label = str(source_label or "")
    if not label.startswith(_FILE_PREFIX):
        return False
    rel = label[len(_FILE_PREFIX):].strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return False
    return rel in _tracked_paths(Path(workspace))


#: Инструменты, чей вывод порождён СОБСТВЕННЫМ зафиксированным деревом. Их
#: блокировка смягчается по тому же основанию, что и у документа: материал наш.
#: `run_tests` печатает исходник падающего теста, а в репозитории есть файлы, где
#: образцы инъекций процитированы намеренно — свидетели. Блокировать такой вывод
#: значило бы ослепить агента на его же красных тестах (MIR-171, тот же дефект).
_REPO_DERIVED_TOOLS = frozenset({"run_tests"})


def block_may_be_annotated(
    tool_name: str | None, source_label: str, workspace: Path | str
) -> bool:
    """Пометить вместо того, чтобы отнять, — только для СВОЕГО материала.

    Своим считается либо файл, лежащий в истории репозитория, либо вывод
    инструмента, порождённого этим же зафиксированным деревом. Подложенное имя
    файла не наше ни по одному из двух признаков — и отводится, как прежде.
    """
    if (tool_name or "") in _REPO_DERIVED_TOOLS:
        return True
    return is_committed_source(source_label, workspace)
