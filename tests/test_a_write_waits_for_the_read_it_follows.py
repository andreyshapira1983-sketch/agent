"""Запись своего текста после свежего чтения в том же пакете ждёт круга.

Находка 3 агента (разговор на сервере 2026-09-22): аргументы шагов фиксируются
при планировании, до исполнения, поэтому текст записи, стоящей после чтения,
сочинён вслепую — в ходе 10:45 тест импортировал несуществующую функцию.
Такая запись и всё после неё откладываются (core/observation_round.py,
`defer_blind_writes`); следующий круг пишет, видя прочитанное.

Находка 4а: «{{step:N.output}}» в прозе записываемого README — не ссылка на шаг
плана и круга не даёт (тот же файл был записан трижды за ход).
"""
from __future__ import annotations

from pathlib import Path

from tests.test_a_read_is_not_the_end_of_the_turn import _events, _loop, _ScriptedPlanner, _src


def test_a_write_after_a_fresh_read_waits_for_the_next_round(workspace: Path):
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    planner = _ScriptedPlanner([
        [_src("file_read", {"path": "numbers.txt"}),
         _src("file_write", {"path": "sum.txt", "content": "угадал: 7"}),
         _src("file_read", {"path": "sum.txt"})],
        [_src("file_write", {"path": "sum.txt", "content": "6"})],
        [],
    ])
    loop = _loop(workspace, planner, observe=True)

    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    assert (workspace / "sum.txt").read_text(encoding="utf-8") == "6"
    deferred = _events(loop, "writes_deferred")
    assert len(deferred) == 1
    assert deferred[0]["deferred"] == ["2. file_write", "3. file_read"], \
        "откладывается слепая запись и проба после неё"
    second = planner.contexts[1]
    assert "1\n2\n3" in second, "второй круг видит прочитанное"
    assert "NOT executed" in second and "угадал: 7" not in second.split("NOT executed")[0], \
        "отложенная запись не выдаётся за выполненную"
    assert "Exact content written to sum.txt" not in second


def test_a_write_before_the_read_and_a_copy_by_reference_run_at_once(workspace: Path):
    (workspace / "src.txt").write_text("x", encoding="utf-8")
    first = _ScriptedPlanner([
        [_src("file_write", {"path": "a.txt", "content": "1"}),
         _src("file_read", {"path": "src.txt"})],
        [],
    ])
    loop = _loop(workspace, first, observe=True)
    loop.run("Запиши 1 в a.txt и прочитай src.txt")
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "1"
    assert not _events(loop, "writes_deferred")

    copy = _ScriptedPlanner([
        [_src("file_read", {"path": "src.txt"}),
         _src("file_write", {"path": "b.txt", "content": "{{step:1.output}}"})],
        [],
    ])
    loop = _loop(workspace, copy, observe=True)
    loop.run("Скопируй src.txt в b.txt")
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "x"
    assert not _events(loop, "writes_deferred"), "перенос по ссылке — не слепой текст"


def test_without_the_flag_nothing_is_deferred(workspace: Path):
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    planner = _ScriptedPlanner([
        [_src("file_read", {"path": "numbers.txt"}),
         _src("file_write", {"path": "sum.txt", "content": "6"})],
    ])
    loop = _loop(workspace, planner, observe=False)
    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")
    assert (workspace / "sum.txt").read_text(encoding="utf-8") == "6"
    assert not _events(loop, "writes_deferred")


def test_a_reference_written_as_prose_does_not_buy_a_round(workspace: Path):
    readme = "Шаги ссылаются друг на друга так: {{step:N.output}} — вывод шага N.\n"
    planner = _ScriptedPlanner([[_src("file_write", {"path": "README.md", "content": readme})], []])
    loop = _loop(workspace, planner, observe=True)

    loop.run("Запиши README.md с этим текстом")

    assert len(planner.contexts) == 1, "проза о ссылках — не ссылка на шаг плана"
    assert (workspace / "README.md").read_text(encoding="utf-8").count("{{step:N.output}}") == 1


def test_on_the_last_round_the_write_is_not_deferred_into_nothing(workspace: Path):
    """2026-09-22 14:09: запись откладывалась на каждом круге, круги кончились —
    файла нет, а ответ сказал «записана». На последнем круге следующего нет."""
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    plan = [_src("file_read", {"path": "numbers.txt"}),
            _src("file_write", {"path": "sum.txt", "content": "6"})]
    planner = _ScriptedPlanner([plan] * 10)
    loop = _loop(workspace, planner, observe=True)

    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    assert (workspace / "sum.txt").read_text(encoding="utf-8") == "6"
