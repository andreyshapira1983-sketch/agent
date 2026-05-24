"""
tools/builtins/calculator.py — Safe arithmetic evaluator

Brain uses this when it needs to compute numbers.
Does NOT eval() arbitrary code — parses only math expressions.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_ALLOWED_OPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}

_MAX_POWER = 1000  # Prevent memory bombs like 2**99999


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate an AST — only allowed numeric operations."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Unsupported literal: {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER:
            raise ValueError(f"Exponent too large: {right}")
        return op(left, right)
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


class CalculatorTool(ToolBase):
    """
    Evaluate a safe arithmetic expression.

    params:
        expression (str): e.g. "2 + 2", "100 / 4", "(3 ** 2) * 5 - 1"
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            description="Evaluate a safe arithmetic expression and return the numeric result",
            parameters={"expression": "str — arithmetic expression, e.g. '2 + 2 * 10'"},
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        expression: str = params.get("expression", "")
        if not expression or not isinstance(expression, str):
            return self._fail("'expression' param is required and must be a string")

        expression = expression.strip()
        if len(expression) > 500:
            return self._fail("Expression too long (max 500 chars)")

        try:
            tree = ast.parse(expression, mode="eval")
            value, elapsed = self._timed(_safe_eval, tree)
            return self._ok(output=value, duration_ms=round(elapsed, 2))
        except (ValueError, ZeroDivisionError) as exc:
            return self._fail(str(exc))
        except SyntaxError as exc:
            return self._fail(f"Syntax error: {exc.msg}")
