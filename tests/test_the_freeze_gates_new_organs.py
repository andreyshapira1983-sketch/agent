"""Заморозка — ворота, а не летопись: новый core-модуль без записи краснит.

Оговорка второго экзаменатора (Codex, 2026-08-28): «реестр исключений честно
документирует нарушение задним числом, но ничего не предотвращает — нет
ворот, требующих зарегистрировать исключение ДО строительства». Эти ворота —
здесь: каждый core-модуль, которого не было в день заморозки (2026-08-20,
базовая линия — коммит 1577b85), обязан быть назван по имени в
docs/audit/AUTONOMY_FREEZE.md — в таблице операторских исключений или в
списке модулей, рождённых хирургией зарегистрированных дефектов. Построить
орган и не записать его стало невозможно тихо.

Базовая линия читается из git-истории тем же узором, что пин дословности
раскола (недоступна история — честный skip, не провал).
"""
from __future__ import annotations

import subprocess  # nosec B404 — fixed argv, reading our own history
from pathlib import Path

import pytest

#: День заморозки: последний коммит 2026-08-20, тот самый, что дописал
#: AUTONOMY_FREEZE.md (MIR-115). Идентификатор прочитан из истории, не по
#: памяти: `git rev-list -1 --before=2026-08-21 HEAD`.
_FREEZE_BASELINE = "1577b85"

_ROOT = Path(__file__).resolve().parents[1]


def _core_modules_at(ref: str) -> set[str] | None:
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv
            ["git", "ls-tree", "--name-only", ref, "core/"],  # noqa: S607
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


def test_every_post_freeze_core_module_is_named_in_the_freeze_doc() -> None:
    baseline = _core_modules_at(_FREEZE_BASELINE)
    if baseline is None:
        pytest.skip("git history unavailable — cannot read the freeze baseline")
    current = {
        f"core/{p.name}" for p in (_ROOT / "core").glob("*.py")
        if p.name != "__init__.py"
    }
    freeze_doc = (_ROOT / "docs" / "audit" / "AUTONOMY_FREEZE.md").read_text(
        encoding="utf-8"
    )

    unrecorded = sorted(
        name for name in current - baseline
        if Path(name).name not in freeze_doc
    )
    assert unrecorded == [], (
        "core-модули построены после заморозки 2026-08-20 и НЕ записаны в "
        "docs/audit/AUTONOMY_FREEZE.md (таблица исключений или список "
        "хирургий): " + ", ".join(unrecorded)
    )
