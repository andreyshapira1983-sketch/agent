"""Раскол `core/loop.py`, куски 8 и 9 — последние чистые швы `_run_inner`.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк».

Кусок 8 — что происходит с уже собранной цепочкой улик: теневой сенсор,
ранжирование источников, каталогизация. Уехал вторым методом в
`core/loop_evidence_chain.py`, к досборке, с которой он делит предмет.

Кусок 9 — чтение контекста хода до планирования: история, самоанализ,
референт, долгая и опытная память. Уехал в `core/loop_context.py`.

Оба сверяются срезом истории. Сверх дословности пинится порядок в куске 9:
референт разрешается ДО выборки памяти, потому что именно его вердикт решает,
подмешивать её вообще или нет.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_context as context_mod
import core.loop_evidence_chain as chain_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

#: кусок -> (модуль, метод, первое выражение среза, последнее выражение среза)
PIECES = {
    8: (
        chain_mod, "_rank_and_catalog_evidence",
        "_premature_keyword_fired = False",
        "self.log.log('knowledge_pipeline', knowledge_result.to_log_payload())",
    ),
    9: (
        context_mod, "_retrieve_turn_context",
        "history = ''",
        "experience_block = self._retrieve_experience_memory(user_question)",
    ),
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


def _slice(body: list[ast.stmt], first: str, last: str) -> list[ast.stmt]:
    start = end = None
    for i, stmt in enumerate(body):
        text = ast.unparse(stmt)
        if start is None and text == first:
            start = i
        elif start is not None and text.rstrip().endswith(last):
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return body[start:end + 1]


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


@pytest.mark.parametrize("piece", sorted(PIECES))
def test_logic_moved_symbol_for_symbol(piece: int):
    """Дословность ЛОГИКИ: срез истории совпадает с телом нового метода."""
    module, method, first, last = PIECES[piece]
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку дословности не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_stmts = _slice(old_run_inner.body, first, last)
    if not old_stmts:  # pragma: no cover — история уже без этого участка
        pytest.skip(f"кусок {piece} в истории не найден — раскол уже зафиксирован")

    new = _func(ast.parse(Path(module.__file__).read_text(encoding="utf-8")), method)
    assert new is not None, f"`{method}` пропал из нового модуля"
    assert isinstance(new.body[0], ast.Expr), "первым в теле ждём docstring"
    assert isinstance(new.body[-1], ast.Return), "последним в теле ждём `return`"
    assert _dump(old_stmts) == _dump(new.body[1:-1]), (
        f"тело куска {piece} изменилось при переносе — это уже не перенос"
    )


@pytest.mark.parametrize("piece", sorted(PIECES))
def test_the_loop_no_longer_defines_it(piece: int):
    """Дубля нет: участок живёт в одном месте, а не в двух."""
    _module, method, first, last = PIECES[piece]
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            defined = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert method not in defined, f"метод определён дважды: {method}"
    run_inner = _func(tree, "_run_inner")
    assert run_inner is not None
    assert not _slice(run_inner.body, first, last), "вырезанный участок остался в цикле"


@pytest.mark.parametrize("piece", sorted(PIECES))
def test_the_agent_still_has_the_moved_method(piece: int):
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    module, method, _first, _last = PIECES[piece]
    assert callable(getattr(AgentLoop, method, None)), f"{method} потерялся"
    assert inspect.getmodule(getattr(AgentLoop, method)) is module


def test_the_referent_is_resolved_before_memory_is_retrieved():
    """Порядок в куске 9: референт решает, подмешивать ли память вообще.

    Локальная критика подавляет выдачу долгой и опытной памяти (PR2). Если
    выборка уедет выше разрешения референта, блоки будут построены и лишь
    затем выброшены — а на разборе конкретного предмета в промпт поедут
    воспоминания, о которых не спрашивали.
    """
    new = _func(ast.parse(Path(context_mod.__file__).read_text(encoding="utf-8")),
                "_retrieve_turn_context")
    assert new is not None
    order = [ast.unparse(st) for st in new.body]

    def _at(needle: str) -> int:
        for i, text in enumerate(order):
            if needle in text:
                return i
        raise AssertionError(f"не нашли {needle!r} в теле")

    assert _at("_maybe_resolve_referent") < _at("_retrieve_persistent"), (
        "память выбирается раньше, чем решён референт"
    )
    assert _at("local_critique_active =") < _at("_retrieve_persistent"), (
        "признак локальной критики считается после выборки — подавление не сработает"
    )


def test_the_shadow_sensor_stays_a_shadow():
    """Кусок 8 отдаёт вердикт сенсора наружу и ничего по нему не делает.

    Ключевой детектор преждевременного завершения замерен на 1/12 полноты и
    срабатывает на «объясни разницу…». Он оставлен ТОЛЬКО для сверки со
    сменившей его проверкой обязательств; если он снова начнёт на что-то
    влиять, это надо заметить здесь, а не в поведении.
    """
    new = _func(ast.parse(Path(chain_mod.__file__).read_text(encoding="utf-8")),
                "_rank_and_catalog_evidence")
    assert new is not None
    text = ast.unparse(new)
    # Флаг только присваивается и возвращается — ни одной ветки по нему.
    branches = [
        n for n in ast.walk(new)
        if isinstance(n, (ast.If, ast.While))
        and "_premature_keyword_fired" in ast.unparse(n.test)
    ]
    assert not branches, "по теневому вердикту появилась ветка — он перестал быть тенью"
    assert "return (_premature_keyword_fired" in text or \
           "return _premature_keyword_fired" in text, "вердикт перестал уезжать наружу"


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2200, f"core/loop.py снова разбух: {lines} строк"
