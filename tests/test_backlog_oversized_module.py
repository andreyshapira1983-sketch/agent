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

from core.backlog_selector import build_backlog, load_backlog
from core.backlog_signals import (
    _MAX_OVERSIZED_RECORDS,
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


def _module_of_short_functions(count: int) -> str:
    return "\n".join(_function_of(10, f"small{i}") for i in range(count))


# ── чистый сканер ─────────────────────────────────────────────────────────────
def test_an_unreadable_function_makes_the_module_a_candidate() -> None:
    records, source = oversized_module_candidates(
        [("core/big.py", _function_of(_UNREADABLE_FUNCTION_LINES))]
    )
    assert len(records) == 1
    rec = records[0]
    assert rec.signal_source == OVERSIZED_MODULE_SOURCE
    assert rec.target_path == "split:core/big.py"
    assert "big" in rec.problem_quote
    assert str(_UNREADABLE_FUNCTION_LINES) in rec.problem_quote
    assert rec.problem_quote in source  # происхождение держится по построению


def test_a_long_file_of_readable_functions_is_left_alone() -> None:
    """Суть замены мерила: длина файла сама по себе поводом больше не служит."""
    long_but_readable = _module_of_short_functions(200)
    assert long_but_readable.count("\n") > 2000

    assert oversized_module_candidates([("core/wide.py", long_but_readable)])[0] == []


def test_the_evidence_points_at_the_function_not_at_line_one() -> None:
    """Читающий должен открыть то, из-за чего дробят."""
    module = _function_of(20, "tiny") + "\n" + _function_of(_UNREADABLE_FUNCTION_LINES)
    records, _ = oversized_module_candidates([("core/mixed.py", module)])

    rel, _, line = records[0].evidence_ref.partition(":")
    assert rel == "core/mixed.py"
    assert int(line) > 20, "улика ведёт в начало файла, а не к длинной функции"


def test_a_method_is_named_with_its_class() -> None:
    """`_execute_step` без класса не найти: одноимённых методов много."""
    module = "class Runner:\n" + _function_of(_UNREADABLE_FUNCTION_LINES, "_step", indent="    ")

    name, size, _ = _longest_function(module)

    assert name == "Runner._step"
    assert size >= _UNREADABLE_FUNCTION_LINES


def test_short_functions_below_the_threshold_are_silent() -> None:
    records, _ = oversized_module_candidates(
        [("core/small.py", _function_of(_UNREADABLE_FUNCTION_LINES - 1))]
    )
    assert records == []


def test_sorted_worst_first() -> None:
    files = [
        ("core/a.py", _function_of(_UNREADABLE_FUNCTION_LINES + 10)),
        ("core/b.py", _function_of(_UNREADABLE_FUNCTION_LINES + 500)),
        ("core/c.py", _function_of(_UNREADABLE_FUNCTION_LINES + 100)),
    ]
    records, _ = oversized_module_candidates(files)
    assert [r.target_path for r in records] == [
        "split:core/b.py",
        "split:core/c.py",
        "split:core/a.py",
    ]


def test_record_cap() -> None:
    files = [
        (f"core/m{i}.py", _function_of(_UNREADABLE_FUNCTION_LINES + i))
        for i in range(_MAX_OVERSIZED_RECORDS + 10)
    ]
    records, _ = oversized_module_candidates(files)
    assert len(records) == _MAX_OVERSIZED_RECORDS


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


# ── ранжирование в общем списке ───────────────────────────────────────────────
def test_oversized_ranks_below_real_work() -> None:
    oversized_records, oversized_text = oversized_module_candidates(
        [("core/huge.py", _function_of(_UNREADABLE_FUNCTION_LINES + 10))]
    )
    tech_debt = "TD-099 — Real actionable debt\nСтатус: Partial\n"
    candidates = build_backlog(
        tech_debt_text=tech_debt,
        oversized_records=oversized_records,
        oversized_text=oversized_text,
    )
    assert candidates[0].signal_source == "tech_debt"
    assert candidates[-1].signal_source == OVERSIZED_MODULE_SOURCE


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
