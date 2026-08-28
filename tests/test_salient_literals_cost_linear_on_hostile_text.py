"""Выделение литералов стоит линейно на враждебном тексте (CodeQL #22).

Второй НАСТОЯЩИЙ ReDoS дня, найденный свежим глазом ожившего сканера:
`_SALIENT_LITERAL_RE` в verifier_absence давал ×16 на вход ×4 по трём
стенам («a-», «a.», «-1») — почти секунда на 16k знаков, а текст сюда
приходит из утверждений ответа, то есть длина внешняя. Мой свип его не
поймал, потому что файл не входил в четвёрку обвинённых — урок: свип по
обвинению уже, чем свип по классу.

Лечение: посессивные кванторы в дефисной ветке (двусмысленные разрезы
умирают) и потолки длины прогонов — реальный литерал короче 160 знаков, а
ограниченный повтор превращает квадрат в линию с константой.
"""
from __future__ import annotations

import time

from core.verifier_absence import _SALIENT_LITERAL_RE


def _best_ms(text: str, attempts: int = 5) -> float:
    best = float("inf")
    for _ in range(attempts):
        t0 = time.perf_counter()
        _SALIENT_LITERAL_RE.findall(text)
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


def test_all_three_measured_walls_grow_linearly() -> None:
    """Красный свидетель: до починки рост был ×15–16 на каждой."""
    for unit, tail in (("a-", "!"), ("a.", "!"), ("-1", "a")):
        small = _best_ms(unit * 2000 + tail)
        large = _best_ms(unit * 8000 + tail)
        growth = large / max(small, 0.05)
        assert growth < 8.0, (
            f"стена '{unit}': вход ×4, время ×{growth:.0f} "
            f"({small:.1f} -> {large:.1f} мс) — квадратура")


def test_real_literals_are_still_caught() -> None:
    """Контроль: цена сбита не ценой работы — все пять видов литералов живы."""
    text = ("запусти run_maintenance_pass, проверь core/loop.py, коммит "
            "9a8d27e1f, версия 2.11.0.3, узел gpt-5-turbo-16k тоже считается")
    found = _SALIENT_LITERAL_RE.findall(text)

    assert "run_maintenance_pass" in found
    assert "core/loop.py" in found
    assert "9a8d27e1f" in found
    assert "2.11.0.3" in found
    assert "gpt-5-turbo-16k" in found


def test_prose_dashes_still_do_not_count() -> None:
    """Измеренная граница держится: «read-only» — проза, не идентификатор."""
    found = _SALIENT_LITERAL_RE.findall("goal-directed and read-only prose")

    assert found == []
