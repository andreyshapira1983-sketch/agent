"""An evidence-free arithmetic answer checked by computing the question's expression (operator, 2026-11-17)."""
from __future__ import annotations

import ast
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_TIMES = re.compile(r"(?<=[\d)])\s*[×xх·]\s*(?=[\d(])")
_EXPR = re.compile(r"[\d\s.()+\-*/%]+")
_HAS_OP = re.compile(r"\d\s*(\*\*|[-+*/%])\s*[\d(]")
_NUMBER = r"-?\d+(?:[.,]\d+)?"
_CUE = re.compile(r"сколько|посчита|вычисл|чему равн|what is|what's|calculate|compute|=", re.IGNORECASE)
_MAX_EXPR, _MAX_OTHER_WORDS, _LIMIT = 120, 8, 10 ** 15


def _value(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _value(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _value(node.left), _value(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 10 ** 6):
            raise ValueError("power too large")
        out = _OPS[type(node.op)](left, right)
        if not isinstance(out, (int, float)) or abs(out) > _LIMIT:
            raise ValueError("not a plain number")
        return out
    raise ValueError("not arithmetic")


def _expression(question: str) -> str | None:
    """The single expression of a short «how much is …» question; anything ambiguous is None."""
    if re.search(r"\d,\d", question) or not _CUE.search(question):
        return None  # decimal comma or thousands group: refused, never guessed (hunter, 17.11)
    text = _TIMES.sub("*", question).replace("÷", "/").replace("^", "**").replace("−", "-")
    found = [m.group().strip(" .") for m in _EXPR.finditer(text)]
    found = [f for f in found if _HAS_OP.search(f)]
    if len(found) != 1 or len(found[0]) > _MAX_EXPR:
        return None
    other = re.findall(r"\w+", text.replace(found[0], " "))
    return found[0] if len(other) <= _MAX_OTHER_WORDS else None


def computed(question: str) -> float | None:
    """The value of the one arithmetic expression in the question, or None."""
    expr = _expression(question)
    if expr is None:
        return None
    try:
        return _value(ast.parse(expr, mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError, RecursionError, MemoryError):
        return None


def _stated(answer: str) -> str | None:
    """The number the whole Conclusion section states: its only number, or the one after a final «=»."""
    lines = [ln.replace("**", "").lstrip("# ") for ln in answer.splitlines()]
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("conclusion:")), None)
    text = answer
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if re.match(r"(?i)(facts|sources|confidence|"
                    r"unverified|safety):", lines[i])), len(lines))
        text = " ".join([lines[start].split(":", 1)[1], *lines[start + 1:end]])
    numbers = re.findall(_NUMBER, text)
    if len(numbers) == 1:
        return numbers[0]
    final = re.search(rf"=\s*({_NUMBER})\s*[.!]?\s*$", text.strip())
    return final.group(1) if final else None


def arithmetic_receipt(question: str, answer: str) -> bool:
    """True when the number the answer states equals the computed value."""
    value, stated = computed(question), _stated(answer)
    if value is None or stated is None:
        return False
    if isinstance(value, int) and re.fullmatch(r"-?\d+", stated):
        return int(stated) == value
    return abs(float(stated.replace(",", ".")) - value) <= 1e-9 * max(1.0, abs(value))
