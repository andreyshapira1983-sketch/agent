"""Раскол `core/loop.py`, кусок 1 — исполнение шага уехало дословно.

Правила оператора (2026-08-03): «разбирай большие файлы на компактные
подключаемые модули — не дублируя, не искажая» и «ни один файл кода не
длиннее 2000 строк». Первый кусок: всё про ОДИН шаг плана (политика,
одобрение, вызов инструмента, разбор результата, классификация, защита от
инъекций, план компенсации, параллельный запуск читающих шагов).

Этот тест — не про поведение (его держит весь остальной набор), а про
ЧЕСТНОСТЬ переноса: тела методов обязаны совпадать с историей символ в
символ по AST, класс обязан оставаться единым для потребителя, а швы —
импорт-пути и цели подмены в тестах — обязаны быть живы.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_step_execution as step_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

#: Что именно уехало. Список — часть контракта: сюда ничего не дописывают
#: молча, иначе «раскол» превращается в свалку.
MOVED = (
    "_execute_steps_parallel",
    "_run_step_parallel",
    "_step_only_reads",
    "_execute_step",
    "_call_tool",
    "_capture_compensation_plan",
    "_request_approval",
)


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


#: Единственная косметика, допущенная сверх переноса, — названа поимённо.
#: Алиас `Literal as _Literal` из истории заставлял pyflakes читать строки
#: внутри `_Literal[...]` как имена («undefined name 'approve'»), а гейт
#: Codacy требует ноль замечаний; подавить было нельзя — второй анализатор
#: тут же звал подавление лишним. Логика при этом не тронута.
COSMETIC_RENAMES = {"_Literal": "Literal"}


def _normalise(dump: str) -> str:
    for old, new in COSMETIC_RENAMES.items():
        dump = dump.replace(f"id='{old}'", f"id='{new}'")
    return dump


def test_logic_moved_symbol_for_symbol():
    """Дословность ЛОГИКИ: тела методов совпадают с историей символ в символ."""
    old_src = subprocess.run(  # nosec B603 — фиксированный argv, без shell
        ["git", "show", "HEAD~1:core/loop.py"],
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку дословности не выполнить")
    old = _methods(ast.parse(old_src), "AgentLoop")
    new = _methods(ast.parse(Path(step_mod.__file__).read_text(encoding="utf-8")))
    for name in MOVED:
        if name not in old:  # pragma: no cover — история уже без метода
            continue
        old_body = "".join(ast.dump(s, include_attributes=False) for s in old[name].body)
        new_body = "".join(ast.dump(s, include_attributes=False) for s in new[name].body)
        assert _normalise(old_body) == _normalise(new_body), (
            f"тело {name} изменилось при переносе — это уже не перенос"
        )


def test_signatures_moved_unchanged():
    """Сигнатуры тоже не тронуты: те же аргументы в том же порядке."""
    old_src = subprocess.run(  # nosec B603 — фиксированный argv, без shell
        ["git", "show", "HEAD~1:core/loop.py"],
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку сигнатур не выполнить")
    old = _methods(ast.parse(old_src), "AgentLoop")
    new = _methods(ast.parse(Path(step_mod.__file__).read_text(encoding="utf-8")))
    for name in MOVED:
        if name not in old:  # pragma: no cover
            continue
        old_args = [a.arg for a in old[name].args.args]
        new_args = [a.arg for a in new[name].args.args]
        assert old_args == new_args, f"сигнатура {name} изменилась при переносе"
        assert _normalise(ast.dump(old[name].returns or ast.Pass())) == _normalise(
            ast.dump(new[name].returns or ast.Pass())
        ), f"возвращаемый тип {name} изменился сверх названной косметики"


def test_the_loop_no_longer_defines_them():
    """Дубля нет: методы живут в одном месте, а не в двух."""
    loop_src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(loop_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            defined = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert not (defined & set(MOVED)), (
                f"метод определён дважды: {sorted(defined & set(MOVED))}"
            )


def test_the_agent_still_has_every_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for name in MOVED:
        assert callable(getattr(AgentLoop, name, None)), f"{name} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, name)) is step_mod


def test_import_seams_survive():
    """Имена, которые импортируют тесты и соседи, доступны по прежнему пути."""
    from core.loop import (
        _TOOL_SOURCE_HINTS,
        _TRUSTED_INTERNAL_TOOLS,
        _step_trigger_tls,
    )

    assert _step_trigger_tls is not None  # шов: имя доступно по прежнему пути
    assert isinstance(_TRUSTED_INTERNAL_TOOLS, frozenset)
    assert _TOOL_SOURCE_HINTS["web_fetch"] == "web"
    # И то же самое — из нового дома.
    assert step_mod._TRUSTED_INTERNAL_TOOLS is _TRUSTED_INTERNAL_TOOLS


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 3600, f"core/loop.py снова разбух: {lines} строк"
