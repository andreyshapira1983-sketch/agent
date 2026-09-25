"""Что будит спящую кампанию: перемена мира, а не ход часов.

Замер 2026-09-25 по журналу кампаний 19–24.09: 109 ожиданий (блок 8,
core/campaign.py), и все 109 кончились по таймеру — ни одного пробуждения по
событию. Будили только два события: сменился состав запрещённых инструментов
(2 строки за пять дней) и новое одобрение (ни разу во время сна). Слово
оператора, новый код и найм на площадке агента не будили: он досыпал до
15 минут, пока мир уже был другим.

Схема — как в практике проактивных агентов (Zylos, «Cadence Control»;
«Proactive AI Agents»): пробуждение по событию плюс редкий запасной таймер.
Таймер остаётся прежним (`_BACKOFF_MAX_SECONDS`); здесь — список событий:

- `world_changed` — журнал перемен способностей (core/capability_events.py);
- `code_changed` — новая правка основной ветки, автор которой не сам агент:
  своя правка агента — не весть извне, иначе он будил бы сам себя;
- `file:<имя>` — файлы из `AGENT_WAKE_FILES` (через `os.pathsep`): разговор
  оператора с агентом, найм на площадке. Какие файлы — решает установка
  сервера, а не код: их пути живут вне рабочей папки агента.

Новое одобрение проверяет сам цикл (у него ящик), здесь его нет.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.capability_events import last_capability_change_ts

WAKE_FILES_ENV = "AGENT_WAKE_FILES"
#: Автор правок самого агента (core/safe_vcs.py, полоса самоправки).
AGENT_GIT_AUTHORS = frozenset({"Self-Apply Lane"})
_MAIN_BRANCHES = ("main", "master")


def _file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _outside_head(workspace: Path) -> str | None:
    """Последняя правка основной ветки не от агента; None — не репозиторий."""
    for branch in _MAIN_BRANCHES:
        try:
            cmd = ["git", "-C", str(workspace), "log", "-30", "--format=%H%x1f%an", branch, "--"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603 — свой git, без оболочки
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            sha, _, author = line.partition("\x1f")
            if author not in AGENT_GIT_AUTHORS:
                return sha
        return ""
    return None


def outside_commit_ts(workspace: str | Path) -> str:
    """Время (ISO, UTC) последней чужой правки основной ветки; "" — неизвестно."""
    sha = _outside_head(Path(workspace))
    if not sha:
        return ""
    try:
        out = subprocess.run(["git", "-C", str(workspace), "show", "-s", "--format=%cI", sha],  # noqa: S603, S607
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        return datetime.fromisoformat(out.stdout.strip()).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def wake_files() -> list[Path]:
    raw = os.environ.get(WAKE_FILES_ENV, "")
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def wake_mark(workspace: str | Path) -> dict[str, Any]:
    """Снимок всех источников пробуждения; сравнивается с прошлым снимком."""
    ws = Path(workspace)
    mark: dict[str, Any] = {"world_changed": last_capability_change_ts(ws),
                            "code_changed": _outside_head(ws)}
    for path in wake_files():
        mark[f"file:{path.name}"] = _file_sig(path)
    return mark


def woken_by(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Какой источник изменился ("" — никакой). Порядок — порядок снимка."""
    return next((key for key, value in after.items() if before.get(key, value) != value), "")
