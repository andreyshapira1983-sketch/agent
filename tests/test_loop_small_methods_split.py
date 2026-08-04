"""Раскол `core/loop.py`, кусок 14 — мелкие методы уехали к своим вызывающим.

Правило оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая». Пять методов, которые оркестровкой не
являются, переехали туда, откуда их зовут: разрешение референта — в
`loop_context`, квитанция проверки — в `loop_verification`, план, сводка
шагов и точка возврата по бюджету — в `loop_attempt`.

Главный тест здесь — не про них пятерых, а про ВЕСЬ раскол: декораторы.
При этом переносе один `@staticmethod` остался в исходном файле и прилип к
следующему методу, а у переехавшего пропал. Причина механическая: у
декорированной функции `lineno` в AST указывает на `def`, а не на декоратор,
так что вырезание «от `lineno` до `end_lineno`» декоратор не захватывает.

Отказ был громким (312 тестов), но громким он оказался случайно — потому что
пострадавший метод звали из горячего пути. `@staticmethod`, потерянный на
методе, который зовут из одной редкой ветки, прошёл бы весь набор молча.
Поэтому сверка ниже идёт по ВСЕМ методам, уехавшим из `AgentLoop`, а не по
этим пяти.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]
_CORE = _REPO / "core"

#: Кто куда уехал этим куском. Список — часть контракта: молча сюда не дописывают.
MOVED = {
    "_maybe_resolve_referent": "core.loop_context",
    "_verification_receipt_kwargs": "core.loop_verification",
    "_build_plan": "core.loop_attempt",
    "_checkpoint_step_summaries": "core.loop_attempt",
    "_save_budget_pause_checkpoint": "core.loop_attempt",
}


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


def _methods_of_agent_loop(src: str) -> dict[str, ast.FunctionDef]:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            return {
                m.name: m for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return {}


def _methods_across_the_split() -> dict[str, tuple[str, ast.FunctionDef]]:
    """Все методы всех примесей цикла: имя -> (модуль, узел)."""
    found: dict[str, tuple[str, ast.FunctionDef]] = {}
    for path in sorted(_CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.setdefault(member.name, (path.name, member))
    return found


def test_every_moved_method_kept_its_decorators():
    """Декораторы переживают переезд — по ВСЕМ методам, а не по названным.

    `lineno` декорированной функции указывает на `def`; вырезание по нему
    оставляет декоратор в старом файле и теряет его в новом. Дважды одно и то
    же поведение это не даёт: `@staticmethod`, потерянный на методе из редкой
    ветки, прошёл бы весь набор молча.
    """
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old = _methods_of_agent_loop(old_src)
    if not old:  # pragma: no cover
        pytest.skip("в истории нет `AgentLoop` — сверять не с чем")
    new = _methods_across_the_split()

    drift = []
    for name, old_node in old.items():
        if name not in new:
            continue
        module, new_node = new[name]
        before = [ast.unparse(d) for d in old_node.decorator_list]
        after = [ast.unparse(d) for d in new_node.decorator_list]
        if before != after:
            drift.append(f"{name} ({module}): было {before}, стало {after}")
    assert not drift, "декораторы разъехались при переносе:\n  " + "\n  ".join(drift)


def test_no_decorator_was_left_behind_without_its_method():
    """В цикле не осталось декоратора, прилипшего к чужому методу.

    Обратная сторона той же ошибки: осиротевший `@staticmethod` не исчезает,
    а достаётся СЛЕДУЮЩЕМУ определению — и меняет его молча.
    """
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [ast.unparse(d) for d in member.decorator_list]
            if "staticmethod" in names:
                args = [a.arg for a in member.args.args]
                assert "self" not in args, (
                    f"{member.name} помечен @staticmethod, но принимает self — "
                    "декоратор достался чужому методу"
                )


def test_each_method_landed_in_its_callers_module():
    """Метод живёт там, откуда его зовут, а не где придётся."""
    for name, module in MOVED.items():
        attr = getattr(AgentLoop, name, None)
        assert attr is not None, f"{name} потерялся"
        # `@staticmethod` доступен через класс уже развёрнутым в функцию,
        # обычный метод — тоже функция: `getmodule` работает для обоих.
        actual = inspect.getmodule(attr)
        assert actual is not None and actual.__name__ == module, (
            f"{name} уехал в {actual.__name__ if actual else '?'}, ожидали {module}"
        )


def test_the_loop_no_longer_defines_them():
    """Дубля нет: каждый метод определён в одном месте."""
    here = set(_methods_of_agent_loop(
        Path(loop_mod.__file__).read_text(encoding="utf-8")
    ))
    assert not (here & set(MOVED)), f"остались в цикле: {sorted(here & set(MOVED))}"


def test_no_method_is_defined_twice_across_the_split():
    """Одно имя — одно определение на все примеси цикла.

    Два определения одного метода в разных примесях означают, что поведение
    выбирает порядок баз в `class AgentLoop(...)`, а не автор.
    """
    seen: dict[str, list[str]] = {}
    for path in sorted(_CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seen.setdefault(member.name, []).append(path.name)
    doubled = {n: mods for n, mods in seen.items() if len(mods) > 1}
    assert not doubled, f"метод определён более одного раза: {doubled}"
