"""Пустой список файлов — это «файлов нет», а не «шаг сломан».

Замер 2026-09-20 по трассам за четверо суток: из 47 снятых шагов 15 сняты с
причиной «python_probe inputs must be a list of paths». При этом промпт
планировщика (core/planner_prompt.py) объявляет ровно такую сигнатуру:
`python_probe(code: str, timeout_seconds: int = 10, inputs: list[str] = [])`
— то есть значение по умолчанию, написанное в договоре, правило допуска
отвергало, и вместе с ним выбрасывало ВЕСЬ замер.

Пустое значение равносильно отсутствию ключа, а одна строка вместо списка
из одной строки — однозначное намерение. Всё остальное по-прежнему
снимается, но теперь жалоба называет, что именно пришло: прежняя её
формулировка не сохраняла значение, и по журналу нельзя было установить,
какой формы был аргумент.
"""
from __future__ import annotations

from core.step_sanitizer import sanitize_step


def _probe(inputs):
    warnings: list[str] = []
    args = {"code": "print(1)"}
    if inputs is not _ABSENT:
        args["inputs"] = inputs
    return sanitize_step("python_probe", args, None, 0, warnings), warnings


_ABSENT = object()


def test_the_documented_default_does_not_kill_the_step() -> None:
    spec, warnings = _probe([])
    assert spec is not None, "значение по умолчанию из договора не ломает шаг"
    assert "inputs" not in spec["arguments"], "пусто значит «файлов нет»"
    assert not warnings


def test_an_empty_string_means_the_same() -> None:
    spec, _warnings = _probe("")
    assert spec is not None and "inputs" not in spec["arguments"]


def test_a_bare_path_is_still_refused() -> None:
    """Границу правки я сначала провёл не там, и полный прогон это поймал.

    Первая редакция достраивала строку до списка одного: «намерение
    однозначно». Но `tests/test_the_lab_computes_over_task_files.py` держит
    обратное СОЗНАТЕЛЬНО, и tools/python_probe.py поднимает ValueError на
    не-список. Строка — тоже последовательность: принятая молча, она
    разошлась бы посимвольно. Договор держат два слоя, и расходиться им
    нельзя. Пустое значение — другое дело: его объявляет сам промпт.
    """
    spec, warnings = _probe("math_study/library/txt/Clark_HonorsCalculus.txt")
    assert spec is None
    assert warnings and "str" in warnings[0]


def test_a_shape_nobody_can_read_is_still_dropped() -> None:
    spec, warnings = _probe({"path": "core/planner.py"})
    assert spec is None
    assert warnings and "dict" in warnings[0], "жалоба обязана назвать, что пришло"


def test_absent_inputs_stay_absent() -> None:
    spec, warnings = _probe(_ABSENT)
    assert spec is not None and "inputs" not in spec["arguments"]
    assert not warnings
