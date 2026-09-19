"""Static-contract tests for the extracted-method mixins.

``core/loop_methods.py`` and ``core/loop_methods2.py`` hold methods lifted out of
``AgentLoop``. They run against state owned by the composed loop, not by
themselves, so each declares what it needs from its host in an
``if TYPE_CHECKING:`` block.

That block is a contract, and unlike ordinary code it can drift from what it
describes in complete silence: nothing inside it ever executes, so no test and
no run will ever notice. These tests read the source and hold it to its own
rules instead.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

MIXIN_SOURCES = [
    # `core/loop_methods.py` разошёлся на пять модулей по ответственностям:
    # три из них (команды памяти, починка, гигиена) вообще не код цикла —
    # их зовут CLI и `agent_tick.py`, а рядом с циклом они жили лишь потому,
    # что оттуда до них дотягивался CLI.
    Path("core/loop_sensor.py"),
    Path("core/loop_knowledge.py"),
    Path("core/loop_memory_commands.py"),
    Path("core/loop_repair.py"),
    Path("core/loop_hygiene.py"),
    # `core/loop_methods2.py` разошёлся на два модуля, названных по делу:
    # он был отвалом автоматического резчика (имя — порядковый номер), а
    # 10 из 11 его методов оказались одной темой — памятью цикла.
    Path("core/loop_memory_read.py"),
    Path("core/loop_memory_write.py"),
]


def _is_type_checking_test(node: ast.expr) -> bool:
    """True for `TYPE_CHECKING` and `typing.TYPE_CHECKING` guards alike."""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


@pytest.mark.parametrize("source", MIXIN_SOURCES, ids=lambda p: p.name)
def test_host_contract_declares_only_members_the_mixin_lacks(source: Path):
    """A host-contract block may only name members the class does NOT define.

    Declaring one it does define replaces the real signature with the stub for
    every static checker -- which is how a loose ``**kwargs`` stub silently
    hides a precise method and makes the class advertise as an external
    requirement something it in fact provides.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    drift: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        declared: set[str] = set()
        defined: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.If) and _is_type_checking_test(stmt.test):
                for inner in stmt.body:
                    if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        declared.add(inner.name)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(stmt.name)
        overlap = declared & defined
        if overlap:
            drift[node.name] = sorted(overlap)

    assert drift == {}, (
        f"{source}: host contract declares members the class defines itself: "
        f"{drift}. Remove the stub; the real definition is the contract."
    )


@pytest.mark.parametrize("source", MIXIN_SOURCES, ids=lambda p: p.name)
def test_every_annotated_name_resolves_in_the_module(source: Path):
    """Names used in annotations must be importable in the module that uses them.

    Annotations on *local* variables are never evaluated, so an unimported name
    there raises nothing at runtime -- it simply makes the annotation a lie no
    reader or checker can follow, and turns into an import-time ``NameError``
    the moment the line moves to module or class scope.
    """
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)

    annotated: set[str] = set()
    for node in ast.walk(tree):
        annotation = getattr(node, "annotation", None) or getattr(
            node, "returns", None
        )
        if annotation is None:
            continue
        for inner in ast.walk(annotation):
            if isinstance(inner, ast.Name):
                annotated.add(inner.id)

    # Everything the module can see: its own imports, definitions and builtins.
    visible: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                visible.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                visible.add(alias.asname or alias.name)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            visible.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            visible.add(node.id)

    unresolved = sorted(annotated - visible)
    assert unresolved == [], (
        f"{source}: annotation names that the module never imports or defines: "
        f"{unresolved}"
    )
