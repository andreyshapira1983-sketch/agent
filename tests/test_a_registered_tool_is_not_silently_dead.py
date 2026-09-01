"""Инструмент, у которого нет ветки в санитайзере шагов, мёртв — и молча.

Замер 2026-08-31, живой: агент впервые сам решил записать вывод в свою память,
планировщик ВЫБРАЛ для этого `memory_bank` — и шаг испарился до исполнения:

    warnings=["step[2]: tool 'memory_bank' has no sanitiser, dropped"]

Хранилище не изменилось, а рапорт агента сообщил об успехе. Регистрация в
`app/bootstrap.py` не означает работоспособности: без ветки в
`core/step_sanitizer.py:sanitize_step` шаг выбрасывается, и наблюдаемо это
только в предупреждении плана. Класс — «registered != operational»: ложный
сигнал доступности, из-за которого агент считает способность имеющейся.

Этот сторож требует ветку для каждого инструмента пояса. Список исключений
пуст намеренно: инструмент, которому ветка не нужна, — это решение, а не
умолчание, и оно должно записываться сюда с причиной.
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Инструменты, ОСОЗНАННО оставленные без ветки санитайзера, с основанием.
#: Пусто: пока таких нет. Добавление сюда — решение с записанной причиной.
_DELIBERATELY_UNSANITISED: dict[str, str] = {}


def _registered_tool_names() -> set[str]:
    """Имена инструментов, вычитанные из исходников пояса, а не из памяти."""
    names: set[str] = set()
    for path in sorted((_REPO / "tools").glob("*.py")):
        if path.name in {"__init__.py", "base.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — живой код обязан разбираться
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "name"
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    names.add(stmt.value.value)
    return names


def _sanitised_tool_names() -> set[str]:
    """Имена, у которых есть ветка `if tool_name == "..."` в санитайзере."""
    source = (_REPO / "core" / "step_sanitizer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id == "tool_name"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(
                comparator.value, str
            ):
                names.add(comparator.value)
            elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                for element in comparator.elts:
                    if isinstance(element, ast.Constant) and isinstance(
                        element.value, str
                    ):
                        names.add(element.value)
    return names


def test_every_registered_tool_has_a_sanitiser_branch() -> None:
    tools = _registered_tool_names()
    assert tools, "инструментов не найдено — сломан сам разбор, а не пояс"

    covered = _sanitised_tool_names() | set(_DELIBERATELY_UNSANITISED)
    dead = sorted(tools - covered)

    assert not dead, (
        "инструмент зарегистрирован, но у него НЕТ ветки в "
        "core/step_sanitizer.py: планировщик сможет его выбрать, а шаг будет "
        "молча выброшен ('has no sanitiser, dropped'). Это ложный сигнал "
        f"доступности способности: {dead}"
    )


def test_the_exception_list_does_not_outlive_its_tools() -> None:
    """Опись исключений, пережившая свой инструмент, тоже лжёт."""
    stale = sorted(set(_DELIBERATELY_UNSANITISED) - _registered_tool_names())

    assert not stale, f"в описи есть инструменты, которых больше нет: {stale}"
