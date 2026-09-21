"""Разбор путей стоит одинаково на дружелюбном и враждебном входе.

Замер, отвергнутые варианты и границы: F-8 в docs/audit/FIELD_CHECK_QUEUE.md, H-10 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import time

import pytest

from core.workspace_reference import _PATH_TOKEN_RE, workspace_paths_named

#: Потолок щедрый нарочно: предмет проверки — форма роста, а не милисекунды на
#: конкретной машине. Катастрофический откат стоит секунды и пробивает его с
#: запасом; форму роста стережёт `test_growth_is_not_quadratic`.
#: 60 → 250 (2026-09-21): замер на этой машине — 6/12/26/51 мс на 1600/3200/
#: 6400/12800 (линейно), а под полной батареей 6400 давал > 60 мс дважды за
#: день и 1 раз из 3 даже в одиночку, при нетронутом модуле. Запас был 2.3×.
_CEILING_MS = 250.0


def _hostile(length: int) -> str:
    """Один непрерывный прогон без разделителя — худший случай для отката."""
    return "A" * length + "_" + "a" * length


def _best_ms(length: int, attempts: int = 5) -> float:
    """Минимум из N замеров — H-10: шум планировщика ОС аддитивен и случаен,
    минимум его срезает; настоящий квадратичный откат детерминирован и не
    спрячется ни в одной попытке. Одиночный замер под полной батареей плакал
    волком трижды за 2026-08-27."""
    best = float("inf")
    for _ in range(attempts):
        started = time.perf_counter()
        _PATH_TOKEN_RE.findall(_hostile(length))
        best = min(best, (time.perf_counter() - started) * 1000)
    return best


@pytest.mark.parametrize("length", [1600, 3200, 6400])
def test_a_hostile_token_stays_cheap(length: int) -> None:
    spent_ms = _best_ms(length)

    assert spent_ms < _CEILING_MS, (
        f"разбор путей стоит {spent_ms:.0f} мс на {length * 2 + 1} знаках — "
        f"стоимость снова растёт быстрее входа, и вход приходит снаружи"
    )


def test_growth_is_not_quadratic() -> None:
    """Существо: важна не абсолютная цена, а ФОРМА роста.

    Учетверение времени на удвоение входа — подпись квадратичного отката, и
    именно она делает выражение оружием против самого агента.
    """
    small, large = _best_ms(1600), _best_ms(6400)  # вход вчетверо
    growth = large / max(small, 0.01)

    assert growth < 8.0, (
        f"вход вырос вчетверо, время — в {growth:.0f} раз: рост "
        f"сверхлинейный ({small:.1f} -> {large:.1f} мс)"
    )


def test_ordinary_paths_are_still_found() -> None:
    """Контроль: цена сбита не ценой работы.

    Без этого предел удовлетворялся бы выражением, которое не находит ничего.
    """
    found = workspace_paths_named(
        "смотри core/workspace_reference.py и docs/audit/FIELD_CHECK_QUEUE.md"
    )

    assert "core/workspace_reference.py" in found
    assert "docs/audit/FIELD_CHECK_QUEUE.md" in found


def test_a_bare_filename_is_still_recognised() -> None:
    """Граница: вторая ветвь выражения — файл без каталога — тоже работает."""
    assert workspace_paths_named("файл main.py в корне") == ["main.py"]
