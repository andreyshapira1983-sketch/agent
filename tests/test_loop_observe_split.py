"""Раскол `core/loop.py`, кусок 6 — начало цикла уехало дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Здесь уезжают фазы §3 «Observe» и «Interpret»: наблюдение,
классификация самого вопроса, маршрут роли, выбор модели и цель.

Сверка та же, что в кусках 2, 4 и 5 — срез тела `_run_inner` против истории.
Сверх дословности пинятся два инварианта, которые в этом участке стоят
дороже строк: классификация вопроса обязана идти ДО всего остального (секрет
в промпте ловится раньше, чем его увидит модель), а `TypeError` из
маршрутизатора моделей обязан пролетать наружу, а не глушиться общим откатом.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_observe as observe_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

MOVED = ("_observe_and_route",)

_SLICE_START = "observation = Observation("
_SLICE_END = "self.log.log('interpret', goal)"


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


def _moved_slice(body: list[ast.stmt]) -> list[ast.stmt]:
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


def _new_method():
    return _func(ast.parse(Path(observe_mod.__file__).read_text(encoding="utf-8")),
                 "_observe_and_route")


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


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
    assert new is not None, "`_observe_and_route` пропал из нового модуля"
    assert isinstance(new.body[0], ast.Expr), "первым в теле ждём docstring"
    assert isinstance(new.body[-1], ast.Return), "последним в теле ждём `return`"
    assert _dump(old_stmts) == _dump(new.body[1:-1]), (
        "тело начала цикла изменилось при переносе — это уже не перенос"
    )


def test_the_loop_no_longer_defines_it():
    """Дубля нет: начало цикла живёт в одном месте, а не в двух."""
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
        assert inspect.getmodule(getattr(AgentLoop, name)) is observe_mod


def test_the_question_is_classified_before_anything_reaches_a_model():
    """Порядок — часть контракта: классификация раньше маршрута и моделей.

    Секрет, вставленный в промпт, ядро обязано поймать ДО того, как его
    увидит модель. Если классификация уедет ниже маршрутизации, поведение
    в обычном прогоне не изменится ни на строку — а гарантия исчезнет.
    """
    new = _new_method()
    assert new is not None
    order = [ast.unparse(st) for st in new.body]

    def _first(needle: str) -> int:
        for i, text in enumerate(order):
            if needle in text:
                return i
        raise AssertionError(f"не нашли {needle!r} в теле")

    classify_at = _first("classify(user_question")
    role_at = _first("self.role_router.route(")
    model_at = _first("self.model_router.for_task(")
    assert classify_at < role_at < model_at, (
        "классификация вопроса обязана идти до маршрута роли и выбора модели"
    )


def test_a_call_signature_defect_in_routing_is_not_laundered():
    """`TypeError` пролетает наружу, всё остальное — откат с записью в журнал.

    Пока `TypeError` глушился общим `except`, каждая задача тихо отвечала на
    модели по умолчанию, и в журнале не было ни строчки о том, что
    маршрутизация перестала работать.
    """
    new = _new_method()
    assert new is not None
    tries = [
        n for n in ast.walk(new)
        if isinstance(n, ast.Try)
        and any("model_router.for_task" in ast.unparse(st) for st in n.body)
    ]
    assert len(tries) == 1, "ожидали один `try` вокруг выбора модели"
    handlers = tries[0].handlers
    names = [ast.unparse(h.type) if h.type else "bare" for h in handlers]
    assert names[0] == "TypeError", "TypeError обязан ловиться первым"
    assert len(handlers[0].body) == 1 and isinstance(handlers[0].body[0], ast.Raise), (
        "TypeError обязан пробрасываться, а не превращаться в откат"
    )
    assert "Exception" in names[1:], "остальные сбои обязаны иметь откат"


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2450, f"core/loop.py снова разбух: {lines} строк"
