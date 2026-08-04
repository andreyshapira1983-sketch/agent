"""Раскол `core/loop.py`, кусок 10 — цикл попыток и объект состояния прогона.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Куски 1–9 переезжали ДОСЛОВНО, символ в символ. Этот — нет, и тест
устроен иначе.

Цикл попыток держится за 22 run-локали `_run_inner`, а `ruff.toml` этого же
репозитория ставит `max-args = 12`: списком параметров он не переносится.
Поэтому состояние названо явно (`AttemptState`), а тело получено из истории
одной подстановкой `имя -> st.имя` по этим 22 полям.

Сила сверки та же, что у дословной, — просто преобразование объявлено:
берём исторический цикл, применяем ТУ ЖЕ подстановку своим кодом и требуем
совпадения AST. Если в теле поменяли хоть один символ сверх подстановки,
тест падает; если подстановку расширили молча — падает тоже, потому что
список полей здесь свой и сверяется с полями датакласса.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_attempt as attempt_mod
from core.loop import AgentLoop
from core.loop_attempt import AttemptState

_REPO = Path(__file__).resolve().parents[1]

#: Ровно те имена, что стали полями состояния. Список ДУБЛИРУЕТ датакласс
#: намеренно: он — вторая независимая запись преобразования, и расхождение
#: между ними обязано быть видно (см. `test_the_substitution_matches_the_state`).
SUBSTITUTED = frozenset({
    "_cp", "_run_assumptions", "_stagnation_shadow", "_task_planner_llm",
    "advice_for_planner", "artifacts", "attempt", "chain", "cheap_path_active",
    "failure_history", "file_hint", "forbidden_actions", "forced_reasoning",
    "forced_sources", "forced_warnings", "goal", "local_critique_active",
    "plan", "planner_history", "planner_out", "replan_exhausted",
    "user_question",
})

#: Имя носителя состояния внутри перенесённого тела.
HOLDER = "st"


#: Коммит ПЕРЕД расколом — источник истины для сверок ниже.
#:
#: Раньше здесь стоял `HEAD`, и это работало ровно до первого коммита: как
#: только раскол попал в историю, `HEAD:core/loop.py` стал расколотым файлом,
#: сверять стало не с чем, и все проверки дословности ушли в `skip`. Отказ был
#: виден в отчёте, но гарантия исчезла бы навсегда — а именно её этот файл и
#: держит. Ссылка закреплена на конкретный коммит, поэтому переезд остаётся
#: проверяемым и через год.
_BEFORE_THE_SPLIT = "76941e061bdd8f85dcb6bdc7ab283262be349f62"


def _history(rev: str = _BEFORE_THE_SPLIT) -> str:
    # Подавления по месту, а не через per-file-ignores: remote-режим Codacy
    # путевые исключения не читает (#300). Argv фиксирован, shell не поднимается.
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "show", f"{rev}:core/loop.py"],  # noqa: S607
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")


def _func(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _the_attempt_loop(fn: ast.AST) -> ast.While | None:
    """Единственный `while True` верхнего уровня в теле — цикл попыток."""
    loops = [
        st for st in fn.body
        if isinstance(st, ast.While)
        and isinstance(st.test, ast.Constant) and st.test.value is True
    ]
    return loops[0] if len(loops) == 1 else None


class _Substitute(ast.NodeTransformer):
    """`имя` -> `st.имя` для объявленных полей. Ровно это и есть перенос."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in SUBSTITUTED:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Name(id=HOLDER, ctx=ast.Load()),
                    attr=node.id,
                    ctx=node.ctx,
                ),
                node,
            )
        return node


def test_the_loop_moved_under_one_declared_substitution():
    """История + объявленная подстановка = то, что лежит в новом модуле."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_loop = _the_attempt_loop(old_run_inner)
    if old_loop is None:  # pragma: no cover — история уже без цикла
        pytest.skip("цикл попыток в истории не найден — раскол уже зафиксирован")

    new_method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
    )
    assert new_method is not None, "`_run_attempt_loop` пропал из нового модуля"
    new_loop = _the_attempt_loop(new_method)
    assert new_loop is not None, "в новом методе должен быть ровно один `while True`"

    expected = ast.fix_missing_locations(_Substitute().visit(ast.parse(ast.unparse(old_loop))))
    got = ast.parse(ast.unparse(new_loop))
    assert ast.dump(expected) == ast.dump(got), (
        "тело цикла отличается от истории СВЕРХ объявленной подстановки — "
        "это уже не перенос"
    )


def test_the_substitution_matches_the_state():
    """Список подстановки и поля датакласса — одно и то же, в обе стороны.

    Без этого предыдущий тест вырождается: расширив `SUBSTITUTED`, можно было
    бы «объявить» любую правку частью переноса.
    """
    declared = {f.name for f in dataclass_fields(AttemptState)}
    assert declared == set(SUBSTITUTED), (
        "поля состояния разошлись со списком подстановки: "
        f"только в датаклассе {sorted(declared - set(SUBSTITUTED))}, "
        f"только в списке {sorted(set(SUBSTITUTED) - declared)}"
    )


def test_the_body_touches_no_run_local_outside_the_state():
    """В теле не осталось голых run-локалей: всё либо в `st`, либо своё.

    Если бы какое-то имя забыли внести в состояние, оно бы читалось из
    ниоткуда — и упало бы уже на прогоне. Но обратный случай тише: имя,
    оставшееся ЛОКАЛЬНЫМ внутри метода, работает, а наружу не попадает. Так
    выход цикла может молча перестать доезжать до вызывающего.
    """
    method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
    )
    assert method is not None
    bound: set[str] = {"self", HOLDER}
    for node in ast.walk(method):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
    assert not (bound & set(SUBSTITUTED)), (
        "имя поля состояния снова связано как локаль: "
        f"{sorted(bound & set(SUBSTITUTED))} — выход цикла не доедет до вызывающего"
    )


def test_the_caller_unpacks_exactly_the_declared_outputs():
    """Что объявлено выходом — вызывающий обязан забрать, и только это.

    Молчаливая потеря выхода — самый тихий способ сломать этот перенос:
    цикл отработает, результат ляжет в `st`, и никто его не заберёт. В обе
    стороны: лишняя распаковка означает, что `OUTPUTS` устарел.
    """
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    unpacked = {
        node.attr for node in ast.walk(run_inner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "_attempt_state"
        and isinstance(node.ctx, ast.Load)
    }
    assert unpacked == set(AttemptState.OUTPUTS), (
        f"распаковка разошлась с объявленными выходами: "
        f"не забрали {sorted(set(AttemptState.OUTPUTS) - unpacked)}, "
        f"забрали лишнее {sorted(unpacked - set(AttemptState.OUTPUTS))}"
    )


def test_every_declared_output_is_actually_written_by_the_loop():
    """Выход, которому цикл ничего не присваивает, — не выход.

    Без этого `OUTPUTS` можно было бы наполнить чем угодно: распаковка
    сойдётся, а смысла в ней не будет.
    """
    method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
    )
    assert method is not None
    written = {
        node.attr for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == HOLDER
        and isinstance(node.ctx, ast.Store)
    }
    idle = sorted(set(AttemptState.OUTPUTS) - written)
    assert not idle, f"объявлены выходом, но цикл их не пишет: {idle}"


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    assert callable(getattr(AgentLoop, "_run_attempt_loop", None))
    assert inspect.getmodule(AgentLoop._run_attempt_loop) is attempt_mod


def test_the_loop_no_longer_defines_it():
    """Дубля нет: цикл попыток живёт в одном месте, а не в двух."""
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    assert _the_attempt_loop(run_inner) is None, "цикл попыток остался в `_run_inner`"


def test_the_operator_ceiling_is_finally_met():
    """Ради чего всё затевалось: файл уложился в операторские 2000 строк."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2000, f"core/loop.py снова выше потолка оператора: {lines} строк"
