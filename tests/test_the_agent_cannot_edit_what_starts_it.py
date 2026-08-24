"""Агент не правит то, чем он запускается и чем его чинят.

ИСТОРИЧЕСКИЙ КЛАСС (H-17, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Facebook/Meta, 4 октября 2021: рутинная проверка пропускной способности
привела к отзыву BGP-анонсов, и вместе с сервисом исчезли ИНСТРУМЕНТЫ
восстановления — внутренний DNS, удалённый доступ, а по сообщениям и
пропуска в помещения. Механизм не «сломалось», а «действие в собственных
полномочиях убрало путь, которым это чинят».

ЗАМЕР 2026-08-24. Разрешительный список полосы самоприменения —
`_ALLOWED_CODE_DIRS = ("core", "cli", "tools", "tests")` — оставляет ВНЕ себя
`agent_tick.py`, `app/bootstrap.py`, `main.py` и весь `scripts/`. То есть
агент не может изменить ни запускающий его тик, ни сборку, ни установщик
демона, ни учение по восстановлению. `.git/` отдельно в чёрном списке, поэтому
история — а с ней и откат — тоже вне досягаемости.

ПОЧЕМУ ЭТО ЗАКРЕПЛЯЕТСЯ ОТДЕЛЬНО. Свойство держится на составе одного кортежа
из четырёх строк. Добавить в него `app` или `scripts` будет выглядеть
безобидным расширением («пусть чинит и свои скрипты»), полная батарея этого не
заметит, и агент получит право переписать собственный запуск — ровно та форма,
в которой Facebook потерял доступ к своему пульту.

ЧТО ЭТОТ ТЕСТ НЕ УТВЕРЖДАЕТ. Он не говорит, что самоприменение безопасно:
`core/self_apply_lane.py` разрешён к правке, то есть агент может менять саму
полосу — это MIR-139, и оно открыто. Здесь только внешний контур: пути, чьё
изменение отняло бы возможность запуститься и откатиться.
"""
from __future__ import annotations

import pytest

from core.self_apply_lane import _is_allowed, _is_denied


def _may_edit(path: str) -> bool:
    return (not _is_denied(path)) and _is_allowed(path)


@pytest.mark.parametrize("path", [
    "agent_tick.py",                 # то, что запускает демон
    "main.py",                       # точка входа
    "app/bootstrap.py",              # сборка агента
    "app/worker_pool.py",
    "scripts/install_daemon.ps1",    # установщик расписания
    "scripts/restore_drill.py",      # учение по восстановлению
    "scripts/check_ceo_file_baseline.py",   # храповики размера
    "scripts/check_function_length_baseline.py",
    "ruff.toml",
    "pytest.ini",
    "pyproject.toml",
    ".git/config",
    ".github/workflows/ci.yml",
])
def test_the_launcher_and_the_repair_path_are_out_of_reach(path: str) -> None:
    assert not _may_edit(path), (
        f"{path} стал доступен самоприменению — действие в собственных "
        "полномочиях теперь может убрать путь, которым агента чинят"
    )


@pytest.mark.parametrize("path", [
    "core/loop.py",
    "cli/commands_health.py",
    "tools/web_fetch.py",
    "tests/test_ids.py",
    "docs/CODE_NOTES.md",
])
def test_ordinary_work_is_still_allowed(path: str) -> None:
    """Граница: запрет обязан оставаться узким, иначе полоса бесполезна."""
    assert _may_edit(path), path
