"""Разбор путей стоит одинаково на дружелюбном и враждебном входе.

СВЕРКА С ПОЛЕМ, форма F-8 «разбор» (docs/audit/FIELD_CHECK_QUEUE.md), первая
половина: стоимость выражения на враждебном входе.

ЗАМЕР 2026-08-25. Развёртка по 226 скомпилированным выражениям, 11 подозрительны
по ФОРМЕ, и только одно подтвердилось ВРЕМЕНЕМ: `_PATH_TOKEN_RE` в
`core/workspace_reference.py` растёт квадратично — 4,7 мс на 801 знаке,
18,3 на 1601, 73,3 на 3201, 293,9 на 6401. Каждое удвоение вчетверо дороже.

ЧЕМ ВЫЗЫВАЕТСЯ. Не длиной текста: 92 КБ обычного английского — 5,8 мс, и
base64 тоже быстра, потому что содержит `/` и выражение сразу успешно. Взрыв
даёт ОДИН непрерывный прогон словесных знаков без разделителя: каждая стартовая
позиция набирает длинный хвост и откатывается, не найдя ни `/`, ни расширения.

ПОЧЕМУ ЗДЕСЬ НЕЛЬЗЯ ОПЕРЕТЬСЯ НА ЧУЖОЙ ПРЕДЕЛ. Класс уже проходили как H-10:
там квадратичные выражения защищены `MAX_EXCERPT_CHARS`, и та же запись честно
называет эту защиту ПОБОЧНОЙ — предел заведён ради памяти, а не ради стоимости
разбора. Здесь он и не работает: `names_workspace_path(question)` в
`doc_routing` получает вопрос целиком, без усечения.

Поэтому ограничено САМО выражение. Отрезок пути длиннее ста двадцати знаков не
бывает рабочим адресом; предел стоит там, где он и есть — в разборе.
"""
from __future__ import annotations

import time

import pytest

from core.workspace_reference import _PATH_TOKEN_RE, workspace_paths_named

#: Потолок щедрый нарочно: предмет проверки — форма роста, а не милисекунды на
#: конкретной машине. Квадратичное выражение пробивает его с запасом.
_CEILING_MS = 60.0


def _hostile(length: int) -> str:
    """Один непрерывный прогон без разделителя — худший случай для отката."""
    return "A" * length + "_" + "a" * length


@pytest.mark.parametrize("length", [1600, 3200, 6400])
def test_a_hostile_token_stays_cheap(length: int) -> None:
    started = time.perf_counter()
    _PATH_TOKEN_RE.findall(_hostile(length))
    spent_ms = (time.perf_counter() - started) * 1000

    assert spent_ms < _CEILING_MS, (
        f"разбор путей стоит {spent_ms:.0f} мс на {length * 2 + 1} знаках — "
        f"стоимость снова растёт быстрее входа, и вход приходит снаружи"
    )


def test_growth_is_not_quadratic() -> None:
    """Существо: важна не абсолютная цена, а ФОРМА роста.

    Учетверение времени на удвоение входа — подпись квадратичного отката, и
    именно она делает выражение оружием против самого агента.
    """
    def _ms(length: int) -> float:
        started = time.perf_counter()
        _PATH_TOKEN_RE.findall(_hostile(length))
        return (time.perf_counter() - started) * 1000

    small, large = _ms(1600), _ms(6400)  # вход вчетверо
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
