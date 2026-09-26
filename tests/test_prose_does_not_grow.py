"""Писанина в core/ (строки комментариев и докстрингов) не растёт: база только вниз."""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1] / "core"

_BASELINE_PROSE_LINES = 18744


def _prose_lines(source: str) -> int:
    lines = source.splitlines()
    prose: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            prose.add(tok.start[0])
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                prose.update(range(body[0].lineno, body[0].end_lineno + 1))
    return sum(1 for n in prose if lines[n - 1].strip())


def _core_prose_lines() -> int:
    return sum(
        _prose_lines(path.read_text(encoding="utf-8")) for path in sorted(_CORE.glob("*.py"))
    )


def test_the_counter_sees_prose() -> None:
    source = '"""Док."""\n# комментарий\nx = 1  # хвост\n\n\ndef f():\n    """Док."""\n'
    assert _prose_lines(source) == 4


def test_prose_does_not_grow() -> None:
    found = _core_prose_lines()
    assert found <= _BASELINE_PROSE_LINES, (
        f"писанина в core/ выросла: {found} > {_BASELINE_PROSE_LINES} строк "
        "комментариев и докстрингов; сократи или объясни рост в сообщении коммита"
    )
