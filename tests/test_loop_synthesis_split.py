"""Раскол `core/loop.py`, кусок 3 — фаза синтеза уехала дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Кусок 1 вынес исполнение шага, кусок 2 — решатели черновика, здесь
уезжает `_synthesize`: сборка промпта синтезатора и вызов модели.

Сверка та же, что в куске 1 (переезжает ЦЕЛЫЙ метод, а не срез тела): тело
и сигнатура обязаны совпасть с историей символ в символ, класс обязан
остаться единым для потребителя, а швы подмены — живыми. Последнее здесь не
формальность: `tests/test_completion_marker.py` подменяет `_synthesize`
через `core.loop.AgentLoop._synthesize`, и этот путь обязан пережить
переезд в миксин.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_synthesis as synthesis_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

#: Что именно уехало. Список — часть контракта: сюда ничего не дописывают
#: молча, иначе «раскол» превращается в свалку.
MOVED = ("_synthesize",)


def _history(rev: str = "HEAD") -> str:
    # Подавления по месту, а не через per-file-ignores: remote-режим Codacy
    # путевые исключения не читает (#300). Argv фиксирован, shell не поднимается.
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "show", f"{rev}:core/loop.py"],  # noqa: S607
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")


def _methods(tree: ast.AST, class_name: str | None = None) -> dict[str, ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (
            class_name is None or node.name == class_name
        ):
            found = {
                m.name: m
                for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name in found for name in MOVED):
                return found
    return {}


def _new() -> dict[str, ast.AST]:
    return _methods(ast.parse(Path(synthesis_mod.__file__).read_text(encoding="utf-8")))


def test_logic_moved_symbol_for_symbol():
    """Дословность ЛОГИКИ: тело метода совпадает с историей символ в символ."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку дословности не выполнить")
    old = _methods(ast.parse(old_src), "AgentLoop")
    new = _new()
    for name in MOVED:
        if name not in old:  # pragma: no cover — история уже без метода
            continue
        old_body = "".join(ast.dump(s, include_attributes=False) for s in old[name].body)
        new_body = "".join(ast.dump(s, include_attributes=False) for s in new[name].body)
        assert old_body == new_body, (
            f"тело {name} изменилось при переносе — это уже не перенос"
        )


def test_signatures_moved_unchanged():
    """Сигнатура не тронута: те же аргументы, значения по умолчанию и тип."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку сигнатур не выполнить")
    old = _methods(ast.parse(old_src), "AgentLoop")
    new = _new()
    for name in MOVED:
        if name not in old:  # pragma: no cover
            continue
        assert ast.dump(old[name].args) == ast.dump(new[name].args), (
            f"сигнатура {name} изменилась при переносе"
        )
        assert ast.dump(old[name].returns or ast.Pass()) == ast.dump(
            new[name].returns or ast.Pass()
        ), f"возвращаемый тип {name} изменился"


def test_the_loop_no_longer_defines_it():
    """Дубля нет: метод живёт в одном месте, а не в двух."""
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


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for name in MOVED:
        assert callable(getattr(AgentLoop, name, None)), f"{name} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, name)) is synthesis_mod


def test_the_patch_seam_by_string_path_survives(monkeypatch):
    """Шов подмены: `core.loop.AgentLoop._synthesize` обязан работать.

    Ровно этим путём `tests/test_completion_marker.py` подменяет синтезатор
    в трёх десятках проверок. Метод переехал в миксин, а атрибут по этому
    имени должен и находиться, и подменяться, и восстанавливаться.
    """
    original = AgentLoop._synthesize

    def _stub(self, *a, **kw):  # pragma: no cover — вызывается не здесь
        return "stub"

    monkeypatch.setattr("core.loop.AgentLoop._synthesize", _stub)
    assert AgentLoop._synthesize is _stub
    monkeypatch.undo()
    assert AgentLoop._synthesize is original
    assert inspect.getmodule(AgentLoop._synthesize) is synthesis_mod


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 1200, f"core/loop.py снова разбух: {lines} строк"


# ── кусок 13: ВЫЗОВ синтезатора (лестница устойчивости) ──────────────────────
# Приехал сюда позже самого `_synthesize` и переезжал не дословно, а под
# объявленной подстановкой `имя -> st.имя` (14 run-локалей против `max-args = 12`
# в `ruff.toml`). Сверка та же по силе, что дословная: применяем ТУ ЖЕ
# подстановку к истории своим кодом и требуем совпадения.

LADDER = "_run_synthesizer_ladder"
LADDER_STATE_VAR = "_synth_state"

#: Ровно те имена, что стали полями `SynthesisState`. Дублирует датакласс
#: намеренно — это вторая независимая запись преобразования.
LADDER_SUBSTITUTED = frozenset({
    "_cp", "_declared", "_task_synth_llm", "artifacts", "cheap_path_active",
    "draft_answer", "failure_history", "file_hint", "goal", "history",
    "local_critique_active", "persistent_block", "plan", "planner_out",
    "replan_exhausted", "user_question",
})


class _LadderSubstitute(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in LADDER_SUBSTITUTED:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Name(id="st", ctx=ast.Load()),
                    attr=node.id,
                    ctx=node.ctx,
                ),
                node,
            )
        return node


def _norm(tree: ast.AST) -> str:
    """AST через `unparse` и обратно.

    Прямой `ast.dump` тут не годится: у `AnnAssign` есть поле `simple` — 1 для
    имени и 0 для атрибута. Подстановка превращает `_declared: … = …` в
    `st._declared: … = …`, и `simple` меняется, хотя поведение нет (локальные
    аннотации не вычисляются и никуда не пишутся). Round-trip это нормализует,
    и сверка остаётся про смысл, а не про поле узла.
    """
    return ast.dump(ast.parse(ast.unparse(tree)), include_attributes=False)


def _ladder_slice(fn: ast.AST) -> list[ast.stmt]:
    """От выбора модели синтезатора до `finally`, восстанавливающего поток."""
    start = end = None
    for i, stmt in enumerate(fn.body):
        text = ast.unparse(stmt)
        # В истории опора зовётся `_task_synth_llm`, в перенесённом теле —
        # `st._task_synth_llm`: одна и та же строка по обе стороны подстановки.
        if start is None and text.endswith(("= _task_synth_llm", "= st._task_synth_llm")):
            start = i
        elif start is not None and isinstance(stmt, ast.Try) and stmt.finalbody:
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return fn.body[start:end + 1]


def test_the_ladder_moved_under_one_declared_substitution():
    """История + объявленная подстановка = то, что лежит в модуле синтеза."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old_run_inner = next(
        (n for n in ast.walk(ast.parse(old_src))
         if isinstance(n, ast.FunctionDef) and n.name == "_run_inner"), None,
    )
    assert old_run_inner is not None, "в истории нет `_run_inner`"
    old = _ladder_slice(old_run_inner)
    if not old:  # pragma: no cover — история уже без участка
        pytest.skip("участок в истории не найден — раскол уже зафиксирован")

    new_method = next(
        (n for n in ast.walk(ast.parse(
            Path(synthesis_mod.__file__).read_text(encoding="utf-8")))
         if isinstance(n, ast.FunctionDef) and n.name == LADDER), None,
    )
    assert new_method is not None, f"`{LADDER}` пропал из модуля синтеза"
    new = _ladder_slice(new_method)
    assert new, "в новом методе не нашли перенесённый участок"

    expected = ast.fix_missing_locations(
        _LadderSubstitute().visit(ast.parse(ast.unparse(ast.Module(body=old, type_ignores=[]))))
    )
    got = ast.Module(body=new, type_ignores=[])
    assert _norm(expected) == _norm(got), (
        "тело лестницы отличается от истории СВЕРХ объявленной подстановки"
    )


def test_the_ladder_substitution_matches_its_state():
    """Список подстановки и поля датакласса — одно и то же, в обе стороны."""
    from dataclasses import fields as dataclass_fields

    from core.loop_synthesis import SynthesisState
    declared = {f.name for f in dataclass_fields(SynthesisState)}
    assert declared == set(LADDER_SUBSTITUTED), (
        "поля состояния разошлись со списком подстановки: "
        f"только в датаклассе {sorted(declared - set(LADDER_SUBSTITUTED))}, "
        f"только в списке {sorted(set(LADDER_SUBSTITUTED) - declared)}"
    )


def test_the_caller_unpacks_exactly_the_declared_outputs():
    """Что объявлено выходом — вызывающий обязан забрать, и только это."""
    from core.loop_synthesis import SynthesisState
    run_inner = next(
        (n for n in ast.walk(ast.parse(
            Path(loop_mod.__file__).read_text(encoding="utf-8")))
         if isinstance(n, ast.FunctionDef) and n.name == "_run_inner"), None,
    )
    assert run_inner is not None
    unpacked = {
        node.attr for node in ast.walk(run_inner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == LADDER_STATE_VAR
        and isinstance(node.ctx, ast.Load)
    }
    assert unpacked == set(SynthesisState.OUTPUTS), (
        "распаковка разошлась с объявленными выходами: "
        f"не забрали {sorted(set(SynthesisState.OUTPUTS) - unpacked)}, "
        f"забрали лишнее {sorted(unpacked - set(SynthesisState.OUTPUTS))}"
    )


def test_a_budget_stop_still_leaves_the_loop():
    """`ModelBudgetExceeded` проходит наружу, а не гасится внутри лестницы.

    Это не сбой синтеза, а конец бюджета: ход обязан встать с сохранённой
    точкой возврата, а не выдать выродившийся ответ. Проглоти его новый
    метод — прогон молча продолжился бы на пустом бюджете.
    """
    method = next(
        (n for n in ast.walk(ast.parse(
            Path(synthesis_mod.__file__).read_text(encoding="utf-8")))
         if isinstance(n, ast.FunctionDef) and n.name == LADDER), None,
    )
    assert method is not None
    handlers = [
        h for n in ast.walk(method) if isinstance(n, ast.Try)
        for h in n.handlers
        if h.type is not None and "ModelBudgetExceeded" in ast.unparse(h.type)
    ]
    assert handlers, "перехват `ModelBudgetExceeded` исчез вместе с сохранением точки"
    for h in handlers:
        assert any(isinstance(s, ast.Raise) for s in h.body), (
            "бюджетная остановка гасится внутри — ход не встанет"
        )


def test_the_agent_still_has_the_ladder():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    assert callable(getattr(AgentLoop, LADDER, None))
    assert inspect.getmodule(getattr(AgentLoop, LADDER)) is synthesis_mod
