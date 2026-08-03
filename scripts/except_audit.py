"""Broad-except audit for ``core/`` (MIR-077) — read-only classifier.

The operator's standing rule: find the ROOT first. Review rounds #283 and
#286 both found the same defect class — a broad ``except Exception`` that
swallows a failure with no journal event, making the subsystem's breakage
invisible. This script maps EVERY broad handler in ``core/`` into classes:

* ``journaled``              — the handler logs/prints something;
* ``reraise``                — the handler re-raises;
* ``log_guard``              — the TRY body is itself only journaling, so a
                               silent handler is the deliberate last-resort
                               guard (the #283/#286 inner pattern);
* ``silent_noop``            — pass/continue/break only;
* ``silent_default_return``  — a bare default return;
* ``silent_other``           — swallows and does something else.

A silent handler WITHOUT a nearby comment naming its reason is the audit's
target class. The pytest ratchet (`tests/test_except_audit_ratchet.py`) pins
the baseline so the class can only shrink.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_LOGGY = (
    "log", "print", "warn", "warning", "info", "error", "debug",
    "critical", "exception", "stderr",
)


def _is_log_call(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    name = ""
    while isinstance(f, ast.Attribute):
        name = f.attr + "." + name if name else f.attr
        f = f.value
    if isinstance(f, ast.Name):
        name = f.id + "." + name if name else f.id
    parts = name.lower().split(".")
    # Token match, not substring: `login()`/`logic()` must not count as
    # journaling (review round #292).
    return any(k in parts for k in _LOGGY)


def _has_log_call(node: ast.AST) -> bool:
    return any(_is_log_call(n) for n in ast.walk(node))


def _broad(handler_type: ast.expr | None) -> bool:
    """Bare, Exception/BaseException by name, or a tuple containing one
    (review round #292: `except (ValueError, Exception):` is just as broad)."""
    if handler_type is None:
        return True
    if isinstance(handler_type, ast.Name):
        return handler_type.id in ("Exception", "BaseException")
    if isinstance(handler_type, ast.Tuple):
        return any(_broad(el) for el in handler_type.elts)
    return False


def _comment_lines(src: str) -> set[int]:
    """1-based line numbers that carry a REAL comment token — a '#' inside a
    string literal is not a comment (review round #292)."""
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):  # pragma: no cover — unparseable snippets
        pass
    return out


def classify_source(src: str, rel_file: str) -> list[dict]:
    comment_lines = _comment_lines(src)
    out: list[dict] = []
    # ast.TryStar (except*) handlers are as capable of swallowing as ast.Try
    # (review round #292).
    try_types = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, try_types):
            continue
        try_only_logs = len(node.body) >= 1 and all(
            isinstance(s, ast.Expr) and _is_log_call(s.value) for s in node.body
        )
        for h in node.handlers:
            if not _broad(h.type):
                continue
            if _has_log_call(h):
                kind = "journaled"
            elif any(isinstance(n, ast.Raise) for n in ast.walk(h)):
                kind = "reraise"
            elif try_only_logs:
                kind = "log_guard"
            elif all(isinstance(s, (ast.Pass, ast.Continue, ast.Break)) for s in h.body):
                kind = "silent_noop"
            elif len(h.body) == 1 and isinstance(h.body[0], ast.Return):
                kind = "silent_default_return"
            else:
                kind = "silent_other"
            # The justification may sit on the line ABOVE the except, on
            # the except line, or anywhere in the handler body — but only a
            # REAL comment token counts, never a '#' inside a string
            # (review round #292).
            end = h.end_lineno or h.body[-1].lineno
            commented = any(
                ln in comment_lines for ln in range(max(1, h.lineno - 1), end + 1)
            )
            out.append({
                "file": rel_file,
                "line": h.lineno,
                "kind": kind,
                "commented": commented,
            })
    return out


def classify_file(path: Path) -> list[dict]:
    return classify_source(
        path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix()
    )


def audit() -> list[dict]:
    rows: list[dict] = []
    # rglob: subdirectories under core/ (none today, but the ratchet must not
    # go blind the day one appears — review round #292).
    for path in sorted((REPO / "core").rglob("*.py")):
        rows.extend(classify_file(path))
    return rows


def unjustified_silent(rows: list[dict]) -> list[dict]:
    """The audit's target class: swallows silently, names no reason."""
    return [
        r for r in rows if r["kind"].startswith("silent") and not r["commented"]
    ]


def main() -> int:
    rows = audit()
    from collections import Counter

    kinds = Counter(r["kind"] for r in rows)
    bad = unjustified_silent(rows)
    print(f"broad except handlers in core/: {len(rows)}")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind}: {count}")
    print(f"silent WITHOUT a stated reason (audit target): {len(bad)}")
    per_file = Counter(r["file"] for r in bad)
    for file, count in per_file.most_common():
        print(f"  {file}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
