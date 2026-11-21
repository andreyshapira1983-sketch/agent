"""Орган самовосприятия: что именно делает модуль кандидатом на разделение.

До 2026-09-23 кандидатом делала ДЛИНА ФАЙЛА: всякий модуль длиннее мягкого
потолка попадал в список. Живой замер того дня показал, чем это кончается.
Механизм предложил разделить `core/loop_step_execution.py` (1158 строк) —
вынести восемь методов по 25-32 строки в модуль «извлечённых методов». Все
двенадцать методов файла относятся к исполнению шага, чужого в нём нет;
линейка файлов этот файл вообще не сторожит (потолок 2000). А `_execute_step`
на 566 строк — ровно то, что прочесть целиком нельзя, — оставался нетронутым.
Двадцать файлов `core/` были кандидатами разом, и агент дробил бы их по
очереди, вынося отовсюду мелочь.

Договор оператора, которым это мерило заменено: файл делится, когда в нём
ЛИШНИЕ функции, к нему не относящиеся, либо когда он так велик, что его не
прочесть и не увидеть, где беда — маленький файл легче наблюдать и связывать.
Машина честно мерит вторую половину: есть ли в модуле функция, которую нельзя
прочесть. Первая половина (чужеродность) мерила пока не имеет и сигналом не
становится — лучше молчать, чем мерить не то.
"""
from __future__ import annotations

from pathlib import Path

from core.backlog_selector import load_backlog
from core.backlog_signals import (
    _UNREADABLE_FUNCTION_LINES,
    OVERSIZED_MODULE_SOURCE,
    _longest_function,
    oversized_module_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _function_of(lines: int, name: str = "big", *, indent: str = "") -> str:
    """Модуль с одной функцией ровно в `lines` строк."""
    body = "\n".join(f"{indent}    x = {i}" for i in range(lines - 1))
    return f"{indent}def {name}():\n{body}\n"


def test_empty_and_none_content_are_safe() -> None:
    records, source = oversized_module_candidates(
        [("", _function_of(2000)), ("core/x.py", None), ("core/y.py", "")]
    )
    assert records == []
    assert source == ""


def test_an_unparseable_module_is_not_called_unreadable() -> None:
    """Немой разбор — не повод дробить и не повод объявить файл здоровым."""
    assert _longest_function("def broken(:\n    pass\n") == ("", 0, 0)
    assert oversized_module_candidates([("core/broken.py", "def broken(:\n")])[0] == []


# ── на живом дереве ───────────────────────────────────────────────────────────
def test_real_repo_surfaces_the_least_readable_function_first() -> None:
    """Худший — первым. Кто именно худший, тест не назначает, а измеряет."""
    candidates = load_backlog(REPO_ROOT)
    oversized = [c for c in candidates if c.signal_source == OVERSIZED_MODULE_SOURCE]
    assert oversized, "в этом дереве нет ни одной нечитаемой функции — проверь мерило"

    def _worst(candidate) -> int:
        rel = candidate.target_path.removeprefix("split:")
        return _longest_function((REPO_ROOT / rel).read_text(encoding="utf-8"))[1]

    sizes = [_worst(c) for c in oversized]
    assert sizes == sorted(sizes, reverse=True), (
        "порядок не «худший первым»: "
        f"{[(c.target_path, n) for c, n in zip(oversized, sizes, strict=True)]}"
    )
    assert sizes[0] >= _UNREADABLE_FUNCTION_LINES
