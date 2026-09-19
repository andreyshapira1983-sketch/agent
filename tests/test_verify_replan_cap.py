"""Регрессия: hard cap верификационного replan обязан быть виден вызывающему."""
from __future__ import annotations

import ast
from pathlib import Path


def _is_exhaustion_assignment(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Attribute)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "st"
        and node.targets[0].attr == "replan_exhausted"
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
    )


def test_verify_replan_hard_cap_marks_replan_exhausted() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "loop_verify_replan.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    hard_cap = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test)
        == "verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP"
    )

    assert _is_exhaustion_assignment(hard_cap.body[0])
