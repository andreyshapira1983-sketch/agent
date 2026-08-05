"""Раскол `core/loop.py`, кусок 2 — решатели черновика уехали дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Кусок 1 (#…) вынес исполнение шага; здесь вынут участок между
синтезом и композицией — шесть решателей, высказывающихся о черновике
ответа.

Отличие от куска 1: там переезжали ЦЕЛЫЕ методы, и сверять было легко.
Здесь вырезан СРЕЗ тела `_run_inner`, поэтому дословность проверяется
иначе — срез истории от `draft = ResponseDraft(body=answer)` до
`answer = draft.render()` обязан совпасть по AST с телом нового метода без
его `return`. Всё остальное — швы: класс остаётся единым для потребителя,
дубля определения нет, точка арбитража (`render`) осталась в цикле.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_response_deciders as deciders_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

#: Что именно уехало. Один метод — но список тут по той же причине, что и в
#: куске 1: сюда ничего не дописывают молча.
MOVED = ("_build_response_draft",)

#: Границы вырезанного среза в историческом `_run_inner`. Названы выражениями,
#: а не номерами строк: номера врут после первого же коммита.
_SLICE_START = "draft = ResponseDraft(body=answer)"
_SLICE_END = "answer = draft.render()"


def _func(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


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


def _moved_slice(body: list[ast.stmt]) -> list[ast.stmt]:
    """Срез истории: от создания черновика до его склейки, конец не включая."""
    start = end = None
    for i, stmt in enumerate(body):
        text = ast.unparse(stmt)
        if start is None and text == _SLICE_START:
            start = i
        elif start is not None and text == _SLICE_END:
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return body[start:end]


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


# ---------------------------------------------------------------------------
# RETIRED: test_logic_moved_symbol_for_symbol
#
# Migration equivalence was verified when the deciders moved out of
# `_run_inner`. That event is over and its proof stands.
#
# Retired 2026-08-05 by the operator's decision, for the same reason as the
# guards in test_loop_verify_replan_split.py and test_loop_context_split.py: a
# finished migration must not be frozen into a git snapshot that forbids lawful
# change. The change it blocked is census item A2 — the answer-enforcement
# handler no longer swallows, and returning the original draft is now forbidden
# by measurement (a reproduced case handed the user a confident unsupported
# claim where the healthy path had written an honest refusal).
#
# What guards this method now, and must not be weakened:
#   * tests/test_answer_enforcement_failure.py — the contract for all six
#     failure points plus the seventh case where the fallback itself breaks.
#   * tests/test_loop_split_wiring.py::test_the_mixin_declares_everything_it_borrows
#   * the import and structure checks below, which are about shape rather than
#     about a snapshot.
# ---------------------------------------------------------------------------

def test_the_loop_no_longer_defines_it():
    """Дубля нет: решатели живут в одном месте, а не в двух."""
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
        assert inspect.getmodule(getattr(AgentLoop, name)) is deciders_mod


def test_arbitration_stayed_in_the_loop():
    """Склейка — по-прежнему в цикле: метод отдаёт черновик, а не текст.

    Смысл `ResponseDraft` в том, что claims и notices соединяются РОВНО в
    одном месте. Если бы вынесенный метод ещё и склеивал, точка арбитража
    уехала бы вместе с ним и следующий решатель молча оказался бы после неё.
    """
    new = _func(ast.parse(Path(deciders_mod.__file__).read_text(encoding="utf-8")),
                "_build_response_draft")
    assert new is not None
    assert "draft.render()" not in ast.unparse(new), "склейка уехала из цикла"

    loop_src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    assert _SLICE_END in loop_src, "цикл больше не склеивает черновик"


def test_the_call_site_passes_every_run_local():
    """Шов вызова: шесть run-локалей, ради которых участок и был отделим."""
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    calls = [
        node for node in ast.walk(run_inner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_build_response_draft"
    ]
    assert len(calls) == 1, "вызов должен быть ровно один"
    call = calls[0]
    assert [ast.unparse(a) for a in call.args] == ["answer"]
    assert {kw.arg for kw in call.keywords} == {
        "user_question", "artifacts", "replan_exhausted",
        "local_critique_active", "verifier_failure",
    }
    # Имена run-локалей передаются как есть — подмены на `self.…` не было.
    assert all(
        isinstance(kw.value, ast.Name) and kw.value.id == kw.arg
        for kw in call.keywords
    ), "аргумент подменён — перенос перестал быть переносом"


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 3200, f"core/loop.py снова разбух: {lines} строк"
