"""Отпечаток проверенного кода: какой именно код сейчас под руками."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: Ветка, с которой сверяемся. Локальная ссылка обновляется при
#: `git fetch`/`git pull`, так что сеть здесь не нужна.
SHARED_REF = "refs/remotes/origin/main"

#: Что считаем кодом при поиске несохранённых правок.
_CODE_SUFFIX = ".py"

#: Каталоги, в которые не заходим: чужое, сгенерированное, тяжёлое.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "htmlcov", "logs", "data",
})

#: Потолок обхода: отпечаток не имеет права стоить дороже самого прогона.
_MAX_FILES_SCANNED = 4000


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    """Хеш по имени ссылки: сначала отдельным файлом, потом packed-refs."""
    direct = _read(git_dir / ref)
    if direct:
        return direct.split()[0]
    packed = _read(git_dir / "packed-refs")
    if not packed:
        return None
    for line in packed.splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0]
    return None


def _files_newer_than_index(root: Path, index: Path) -> int | None:
    """Сколько .py новее индекса — дешёвый признак несохранённых правок.

    Это не `git status`: переименования, удаления и правки, уже добавленные
    в индекс, сюда не попадают. Поле названо ровно тем, что измеряет.
    """
    try:
        index_mtime = index.stat().st_mtime
    except OSError:
        return None
    seen = newer = 0
    # os.walk, а не rglob: пропускаемые каталоги отсекаются ДО спуска в них.
    # rglob обходил .venv и node_modules целиком и лишь потом отбрасывал
    # результат — отпечаток стоил дороже самого прогона (замечание ревью #301).
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(_CODE_SUFFIX):
                continue
            seen += 1
            if seen > _MAX_FILES_SCANNED:
                return newer
            try:
                if (Path(current) / name).stat().st_mtime > index_mtime:
                    newer += 1
            except OSError:
                continue
    return newer


def describe_code_state(root: Path | str) -> dict[str, Any]:
    """Чем является код в ``root`` прямо сейчас."""
    path = Path(root).resolve()
    state: dict[str, Any] = {
        "workspace": str(path),
        "commit": None,
        "branch": None,
        "shared_commit": None,
        "matches_shared": None,
        "files_newer_than_index": None,
    }

    git_dir = path / ".git"
    if not git_dir.is_dir():
        state["reason"] = "не git-дерево"
        return state

    head = _read(git_dir / "HEAD")
    if not head:
        state["reason"] = "HEAD нечитаем"
        return state

    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        # Полное имя после refs/heads/: у ветки `fix/introspection-routing`
        # обрезка по последнему слэшу оставляла бы «routing» — отпечаток врал
        # бы о том, где работали (замечание ревью #301).
        state["branch"] = ref.removeprefix("refs/heads/")
        state["commit"] = _resolve_ref(git_dir, ref)
        if state["commit"] is None:
            state["reason"] = "в ветке ещё нет коммитов"
            return state
    else:
        state["commit"] = head.split()[0]

    shared = _resolve_ref(git_dir, SHARED_REF)
    if shared:
        state["shared_commit"] = shared
        state["matches_shared"] = shared == state["commit"]
    else:
        state["reason"] = f"общая ветка {SHARED_REF} локально неизвестна"

    state["files_newer_than_index"] = _files_newer_than_index(path, git_dir / "index")
    return state
