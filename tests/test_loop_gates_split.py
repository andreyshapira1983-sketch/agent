"""Раскол `core/loop.py`, кусок 15 — ранние выходы уехали в ворота.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая». Четыре места, где ход может закончиться
до планирования: домен применимости (§7 ODD), политика уточнений (§3),
быстрый путь по эпизодам и отказ многофайлового разбора.

Приём отличается от всех предыдущих кусков. Внутри этих блоков `return`, а
`return` из помощника — не выход из цикла: перенеси его как есть, и ход
поехал бы дальше там, где обязан был остановиться, причём молча и только на
редких ветках. Поэтому каждый метод отдаёт `str | None`, а решение вернуть
принимает вызывающий.

Отсюда и главный тест здесь — не про дословность (она тоже есть), а про то,
что `None` означает «продолжай», а не «ответ пустой»: вызывающий обязан
сравнивать с `None`, а не проверять истинность. Пустая строка от ворот — это
всё ещё решённый ход, и `if _decided:` проглотил бы его.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_gates as gates_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

#: метод -> первая строка перенесённого тела (опора в истории).
GATES = {
    "_odd_gate": "if self.odd_enabled:",
    "_clarification_gate": "if self.clarification_enabled:",
    "_episodic_fast_path": "_fp_ep = self._last_best_similar_episode",
    "_multi_file_refusal": "if multi_file['kind'] == 'refusal':",
}


def _history(rev: str = "HEAD") -> str:
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


def _gates_tree() -> ast.AST:
    return ast.parse(Path(gates_mod.__file__).read_text(encoding="utf-8"))


@pytest.mark.parametrize("method", sorted(GATES))
def test_the_body_moved_symbol_for_symbol(method: str):
    """Дословность: тело совпадает с историей, добавлен только `return None`."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner`"
    anchor = GATES[method]
    old_stmts = [s for s in old_run_inner.body if ast.unparse(s).startswith(anchor)]
    if not old_stmts:  # pragma: no cover — история уже другая
        pytest.skip(f"опора {anchor!r} в истории не найдена")

    new = _func(_gates_tree(), method)
    assert new is not None, f"`{method}` пропал из модуля ворот"
    body = new.body
    assert isinstance(body[0], ast.Expr), "первым в теле ждём docstring"
    assert isinstance(body[-1], ast.Return) and ast.unparse(body[-1]) == "return None", (
        "последним в теле ждём `return None` — провал сквозь означает «я не при делах»"
    )
    moved = body[1:-1]
    # У быстрого пути перед блоком есть две строки подготовки — они тоже
    # перенесены; сверяем хвост, начиная с опоры.
    start = next(i for i, s in enumerate(moved) if ast.unparse(s).startswith(anchor))
    expected = old_run_inner.body[old_run_inner.body.index(old_stmts[0]):][: len(moved) - start]
    got = moved[start:]
    assert "".join(ast.dump(s, include_attributes=False) for s in expected) == \
           "".join(ast.dump(s, include_attributes=False) for s in got), (
        f"тело {method} изменилось при переносе — это уже не перенос"
    )


@pytest.mark.parametrize("method", sorted(GATES))
def test_every_gate_returns_a_decision_or_none(method: str):
    """Подпись — `str | None`, и `None` действительно достижим."""
    new = _func(_gates_tree(), method)
    assert new is not None
    assert ast.unparse(new.returns) == "str | None", (
        f"{method} обязан возвращать `str | None`: строка — решение, None — «продолжай»"
    )
    assert ast.unparse(new.body[-1]) == "return None"


def test_the_caller_compares_with_none_not_truthiness():
    """`if _decided is not None`, а не `if _decided`.

    Ворота вправе решить ход пустым ответом — например, отказ, у которого
    сообщение пустое. `if _decided:` проглотил бы такое решение и поехал
    планировать дальше: ход, обязанный остановиться, продолжился бы молча.
    """
    run_inner = _func(
        ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")), "_run_inner"
    )
    assert run_inner is not None
    guards = [
        node for node in ast.walk(run_inner)
        if isinstance(node, ast.If) and "_decided" in ast.unparse(node.test)
    ]
    assert len(guards) == len(GATES), (
        f"ожидали {len(GATES)} проверок решения, нашли {len(guards)}"
    )
    for guard in guards:
        assert isinstance(guard.test, ast.Compare) and isinstance(
            guard.test.ops[0], ast.IsNot
        ), f"проверка решения не через `is not None`: {ast.unparse(guard.test)}"
        assert any(isinstance(s, ast.Return) for s in guard.body), (
            "решение ворот не возвращается — ход поедет дальше"
        )


def test_each_gate_is_called_exactly_once():
    """Каждые ворота зовут ровно один раз, и решение забирают."""
    run_inner_src = inspect.getsource(loop_mod.AgentLoop._run_inner)
    for method in GATES:
        assert run_inner_src.count(f"self.{method}(") == 1, (
            f"{method} зовут не один раз — порядок ворот перестал быть очевидным"
        )


def test_the_agent_still_has_every_gate():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for method in GATES:
        assert callable(getattr(AgentLoop, method, None)), f"{method} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, method)) is gates_mod


def test_the_replay_helpers_stayed_where_they_name_their_class():
    """`_*_allows_replay` остались в цикле — и это названное решение.

    Обе статические функции именуют `AgentLoop` прямо в теле (пороги живут
    константами на классе). Переезд означал бы правку тела, то есть уже не
    перенос; вместо этого они разрешаются по MRO, как и всё остальное.
    """
    loop_src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    for name in ("_fast_path_allows_replay", "_quality_allows_replay"):
        assert f"def {name}(" in loop_src, f"{name} уехал — проверьте, не правили ли тело"
        assert inspect.getmodule(getattr(AgentLoop, name)) is loop_mod
    # Ищем по AST, а не по тексту: в комментариях `AgentLoop.__init__`
    # упоминается законно — там объясняется, кто создаёт поля хоста.
    referenced = [
        ast.unparse(node) for node in ast.walk(_gates_tree())
        if isinstance(node, ast.Name) and node.id == "AgentLoop"
    ]
    assert not referenced, (
        f"ворота ссылаются на класс по имени ({referenced}) — путь к круговому импорту"
    )
