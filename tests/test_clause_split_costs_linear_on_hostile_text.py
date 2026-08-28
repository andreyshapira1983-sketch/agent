"""Раскрой предложений стоит линейно и на враждебном тексте (CodeQL #19/20).

Единственный НАСТОЯЩИЙ из пяти ReDoS-алертов (свип 2026-08-28 по методике
H-10: все выражения четырёх обвинённых файлов × десять стен): ветка `\\s+—`
в `_CLAUSE_SPLIT_RE` заново съедала хвост с каждого старта — стена пробелов
давала рост ×14.5 при входе ×4. Текст сюда приходит из ЧУЖИХ ответов модели,
то есть вход внешний — класс H-10 (Cloudflare 2019) буквально.

Лечение — якорь на самом тире: `(?<=\\s)—\\s+` стартует только у «—»,
предпробельный хвост уходит в strip у потребителя. Поведение эквивалентно
и приколочено здесь же примерами, включая многопробельное тире.
"""
from __future__ import annotations

import time

from core.completion_contract import demanding_text


def _best_ms(text: str, attempts: int = 5) -> float:
    best = float("inf")
    for _ in range(attempts):
        t0 = time.perf_counter()
        demanding_text(text)
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


def test_a_space_wall_grows_linearly() -> None:
    """Красный свидетель: до починки рост был ×14.5 на вход ×4."""
    small = _best_ms(" " * 4000 + "a")
    large = _best_ms(" " * 16000 + "a")

    growth = large / max(small, 0.05)
    assert growth < 8.0, (
        f"вход вырос вчетверо, время — в {growth:.0f} раз "
        f"({small:.1f} -> {large:.1f} мс): квадратура вернулась")


def test_splitting_behaviour_is_unchanged() -> None:
    """Эквивалентность: те же удержанные части на представительных текстах."""
    assert demanding_text("Сделай A. Потом B.") == "Сделай A. Потом B."
    assert demanding_text("часть один — часть два") == "часть один часть два"
    assert demanding_text("хвост  —  с двойными пробелами") == "хвост с двойными пробелами"
    assert demanding_text("слово—без пробелов не делится") == "слово—без пробелов не делится"
