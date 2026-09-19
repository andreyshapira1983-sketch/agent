"""Заморозка — датчик раскрытия: новый кодовый файл без записи краснит.

Оговорка второго экзаменатора (Codex, 2026-08-28): реестр исключений сам по
себе ничего не предотвращает. Этот тест — механизированная половина ответа,
и его имя честное: это ДАТЧИК РАСКРЫТИЯ, не ворота разрешения. Предварительное
разрешение — свойство процесса (слово оператора до стройки), и репозиторий
может принудить только к записи, не к разрешению: тест живёт в том же дереве,
что и код, и обгонять стройку не умеет. Что он гарантирует: файл, рождённый
после заморозки в любом кодовом доме (core/, cli/, app/, tools/, api/,
включая подкаталоги) и не названный по имени в docs/audit/AUTONOMY_FREEZE.md,
валит батарею — тихой стройки не бывает.

Честные слепые пятна, названные экзаменатором и не закрытые: новая
функциональность ВНУТРИ старого модуля структурному датчику не видна
(семантика, не имена); при недоступной git-истории — skip, но оба живых
прогонщика историю имеют (домашний клон полный; CI качает fetch-depth: 0
ради gitleaks — проверено 2026-08-28).

Базовая линия — последний коммит дня заморозки (2026-08-20), прочитан из
истории: `git rev-list -1 --before=2026-08-21 HEAD`.
"""
from __future__ import annotations

import subprocess  # nosec B404 — fixed argv, reading our own history
from pathlib import Path

import pytest

_FREEZE_BASELINE = "1577b85"
_CODE_HOMES = ("core", "cli", "app", "tools", "api")

_ROOT = Path(__file__).resolve().parents[1]


def _py_files_at(ref: str) -> set[str] | None:
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv
            ["git", "ls-tree", "-r", "--name-only", ref, *_CODE_HOMES],  # noqa: S607
            cwd=str(_ROOT), capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = {
        line.strip() for line in out.stdout.decode("utf-8").splitlines()
        if line.strip().endswith(".py")
    }
    return names or None


def test_every_post_freeze_code_file_is_named_in_the_freeze_doc() -> None:
    baseline = _py_files_at(_FREEZE_BASELINE)
    if baseline is None:
        pytest.skip("git history unavailable — cannot read the freeze baseline")
    current = {
        p.relative_to(_ROOT).as_posix()
        for home in _CODE_HOMES
        for p in (_ROOT / home).rglob("*.py")
        if (_ROOT / home).is_dir() and "__pycache__" not in p.parts
        and p.name != "__init__.py"
    }
    freeze_doc = (_ROOT / "docs" / "audit" / "AUTONOMY_FREEZE.md").read_text(
        encoding="utf-8"
    )

    unrecorded = sorted(
        name for name in current - baseline
        if Path(name).name not in freeze_doc
    )
    assert unrecorded == [], (
        "кодовые файлы построены после заморозки 2026-08-20 и НЕ записаны в "
        "docs/audit/AUTONOMY_FREEZE.md (таблица исключений или список "
        "хирургий): " + ", ".join(unrecorded)
    )
