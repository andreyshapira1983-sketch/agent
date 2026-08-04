"""Раскол `core/loop.py`, кусок 4 — досборка цепочки улик уехала дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Куски 1–3 вынесли исполнение шага, решателей черновика и синтез;
здесь уезжает участок, где в цепочку досыпаются три источника, приходящие
не через шаги плана: долгая память, рабочая память прошлых ходов и диалог.

Как и в куске 2, вырезан СРЕЗ тела `_run_inner`, поэтому дословность
сверяется срезом истории. Отдельно пинится гранулярность защиты: `try`
обязан стоять ВНУТРИ каждого из трёх циклов, а не вокруг них — MIR-061 был
ровно про это, и цена ошибки (молча урезанная цепочка) не видна в поведении
до самого верификатора.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_evidence_chain as chain_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

MOVED = ("_fold_evidence_chain",)

#: Границы вырезанного среза в историческом `_run_inner`, названы выражениями:
#: номера строк врут после первого же коммита.
_SLICE_START = "if persistent_block and self.persistent_store is not None:"
_SLICE_END = "self.last_provenance = chain"


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


def _moved_slice(body: list[ast.stmt]) -> list[ast.stmt]:
    """Срез истории: от первой досыпки до укладки цепочки на цикл, включая."""
    start = end = None
    for i, stmt in enumerate(body):
        text = ast.unparse(stmt)
        if start is None and text.startswith(_SLICE_START):
            start = i
        elif start is not None and text == _SLICE_END:
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return body[start:end + 1]


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


def _new_method():
    return _func(ast.parse(Path(chain_mod.__file__).read_text(encoding="utf-8")),
                 "_fold_evidence_chain")


def test_logic_moved_symbol_for_symbol():
    """Дословность ЛОГИКИ: срез истории совпадает с телом нового метода."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку дословности не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_stmts = _moved_slice(old_run_inner.body)
    if not old_stmts:  # pragma: no cover — история уже без этого участка
        pytest.skip("участок в истории не найден — раскол уже зафиксирован")

    new = _new_method()
    assert new is not None, "`_fold_evidence_chain` пропал из нового модуля"
    assert isinstance(new.body[0], ast.Expr), "первым в теле ждём docstring"
    assert _dump(old_stmts) == _dump(new.body[1:]), (
        "тело досборки изменилось при переносе — это уже не перенос"
    )


def test_the_loop_no_longer_defines_it():
    """Дубля нет: досборка живёт в одном месте, а не в двух."""
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            defined = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert not (defined & set(MOVED)), (
                f"метод определён дважды: {sorted(defined & set(MOVED))}"
            )
    run_inner = _func(tree, "_run_inner")
    assert run_inner is not None
    assert not _moved_slice(run_inner.body), "вырезанный участок остался в цикле"


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for name in MOVED:
        assert callable(getattr(AgentLoop, name, None)), f"{name} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, name)) is chain_mod


def test_the_guard_is_per_record_not_around_the_loop():
    """MIR-061: `try` внутри каждого цикла, а не вокруг него.

    С `try` снаружи одна незашедшая запись бросала весь цикл и всё, что
    шло за ней, исчезало из цепочки молча — замерено 1 из 5 доехавших.
    Поведенчески это неотличимо от честной нехватки улик, поэтому
    гранулярность пинится структурно, а не прогоном.
    """
    new = _new_method()
    assert new is not None
    loops = [n for n in ast.walk(new) if isinstance(n, ast.For)]
    assert len(loops) == 3, f"ожидали три цикла досыпки, нашли {len(loops)}"
    for loop in loops:
        assert any(isinstance(st, ast.Try) for st in loop.body), (
            "цикл без `try` внутри: одна плохая запись снова унесёт остальные"
        )
    # И наоборот: ни один из циклов не обёрнут в `try` целиком.
    for node in ast.walk(new):
        if isinstance(node, ast.Try):
            assert not any(isinstance(st, ast.For) for st in node.body), (
                "цикл целиком под `try` — это и есть форма дефекта MIR-061"
            )


def test_the_chain_is_mutated_in_place():
    """Контракт `-> None`: новой цепочки не возникает, ссылка та же.

    Вызывающий продолжает работать с тем же объектом и после вызова кладёт
    его в верификатор. Если бы метод собирал новую цепочку и не возвращал
    её, досыпанные улики просто не доехали бы — а тесты про цитаты упали бы
    далеко от причины.
    """
    new = _new_method()
    assert new is not None
    assert ast.unparse(new.returns) == "None"
    assert not [n for n in ast.walk(new) if isinstance(n, ast.Return) and n.value]
    # `chain` нигде не переприсваивается — только .add()
    assert not [
        n for n in ast.walk(new)
        if isinstance(n, ast.Name) and n.id == "chain" and isinstance(n.ctx, ast.Store)
    ], "цепочка переприсвоена — вызывающий останется со старой"


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2700, f"core/loop.py снова разбух: {lines} строк"
