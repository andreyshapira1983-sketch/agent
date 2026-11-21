"""Read-only classifier of broad except handlers; flags silent ones that state no reason."""
from __future__ import annotations

import ast
import io
import re
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
    # Underscores are stripped first, so a private helper like `self._log` is journaling;
    # `_login`/`_logic` become `login`/`logic`, which still are not.
    parts = [p.lstrip("_") for p in name.lower().split(".")]
    # `log_` with the separator admits log_error/log_summary and still refuses login/logic.
    return any(k in parts for k in _LOGGY) or any(p.startswith("log_") for p in parts)


#: Helpers that journal on the caller's behalf (`_sensor_failed` -> core/sensor_journal.py).
_REPORTING_HELPERS = frozenset({"_sensor_failed"})


def _is_reporting_call(n: ast.AST) -> bool:
    """A journal write, or a helper that performs one."""
    if _is_log_call(n):
        return True
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _REPORTING_HELPERS
    )


def _has_log_call(node: ast.AST) -> bool:
    return any(_is_reporting_call(n) for n in ast.walk(node))


def _broad(handler_type: ast.expr | None) -> bool:
    """Bare, Exception/BaseException by name, or a tuple containing one of them."""
    if handler_type is None:
        return True
    if isinstance(handler_type, ast.Name):
        return handler_type.id in ("Exception", "BaseException")
    if isinstance(handler_type, ast.Tuple):
        return any(_broad(el) for el in handler_type.elts)
    return False


#: A bare linter directive silences the tool that asked for a reason; it is not a reason.
_DIRECTIVE_ONLY_RE = re.compile(
    r"^#\s*(?:noqa|type:|pragma|pylint:|mypy:|ruff:)[^#]*$", re.IGNORECASE
)


def _is_bare_directive(text: str) -> bool:
    """True when a comment carries only tool directives; a directive plus a written why is not."""
    stripped = text.strip()
    if not _DIRECTIVE_ONLY_RE.match(stripped):
        return False
    remainder = re.sub(
        r"(?i)(?:noqa|type|pragma|pylint|mypy|ruff)\s*:?\s*[A-Z]*\d*[,\s]*", "",
        stripped.lstrip("#").strip(),
    )
    return len(re.sub(r"[^\wЀ-ӿ]+", "", remainder)) < 12


def _comment_lines(src: str) -> set[int]:
    """1-based lines with a real comment token (not a '#' in a string, not a bare directive)."""
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and not _is_bare_directive(tok.string):
                out.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):  # pragma: no cover — unparseable snippets
        pass
    return out


def classify_source(src: str, rel_file: str) -> list[dict]:
    comment_lines = _comment_lines(src)
    out: list[dict] = []
    # except* (ast.TryStar) handlers can swallow just like ast.Try ones.
    try_types = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, try_types):
            continue
        try_only_logs = len(node.body) >= 1 and all(
            isinstance(s, ast.Expr) and _is_reporting_call(s.value) for s in node.body
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
            # A reason may sit on the line above the except, on it, or in the handler body.
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


def _unconditional_reporters(tree: ast.AST) -> frozenset[str]:
    """Functions in this file that ALWAYS write to the journal when called.

    A write inside an `if` does not count (other paths stay silent); one inside `try`/`with`
    does. Nested definitions are skipped: a closure's log call belongs to the closure.
    """
    out: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        def _always_logs(body: list[ast.stmt]) -> bool:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(stmt, ast.Expr) and _is_reporting_call(stmt.value):
                    return True
                if isinstance(stmt, ast.Try) and _always_logs(stmt.body):
                    return True
                if isinstance(stmt, ast.With) and _always_logs(stmt.body):
                    return True
            return False

        if _always_logs(fn.body):
            out.add(fn.name)
    return frozenset(out)


def journal_silent_handlers(src: str, rel_file: str) -> list[dict]:
    """Handlers that write NOTHING to the journal, per file.

    Unlike classify_source, comments do not count: they do not help an operator reading logs.
    Excluded: handlers that re-raise, guards around a journal write, and handlers nested in
    one that reports (reporting a failed report would be a recursion).
    """
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    local_reporters = _unconditional_reporters(tree)

    def _reports(handler: ast.ExceptHandler) -> bool:
        for n in ast.walk(handler):
            if _is_reporting_call(n):
                return True
            # Delegating the report to a local always-journaling helper counts as reporting.
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in local_reporters
            ):
                return True
        return False

    # A try whose body is only journal writes: its handler guards reporting itself (the same
    # rule as try_only_logs in classify_source).
    guards_a_write: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if node.body and all(
            isinstance(s, ast.Expr) and _is_reporting_call(s.value) for s in node.body
        ):
            guards_a_write.update(h.lineno for h in node.handlers)

    out: list[dict] = []
    for handler in handlers:
        if _reports(handler):
            continue
        if handler.lineno in guards_a_write:
            continue
        if any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
            continue
        span_start, span_end = handler.lineno, handler.end_lineno or handler.lineno
        nested_in_reporting = any(
            other is not handler
            and other.lineno <= span_start
            and (other.end_lineno or other.lineno) >= span_end
            and _reports(other)
            for other in handlers
        )
        if nested_in_reporting:
            continue
        out.append({"file": rel_file, "line": span_start})
    return out


def loop_layer_files(root: Path) -> list[Path]:
    """The loop layer: loop*.py plus modules they import as ``import <pkg>.x as y``.

    Extracted parts stay in scope, so moving code out of loop*.py cannot look like a repair.
    """
    files = sorted(p for p in root.glob("loop*.py") if "__pycache__" not in p.parts)
    extracted: set[Path] = set()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import):
                continue
            for alias in node.names:
                if alias.asname is None or not alias.name.startswith(f"{root.name}."):
                    continue
                candidate = root / f"{alias.name.split('.', 1)[1]}.py"
                if candidate.exists():
                    extracted.add(candidate)
    return sorted(set(files) | extracted)


def journal_silent_in(root: Path, pattern: str = "*.py") -> list[dict]:
    """Every journal-silent handler under ``root``, sorted for stable output."""
    rows: list[dict] = []
    for path in sorted(root.rglob(pattern)):
        if "__pycache__" in path.parts:
            continue
        try:
            rows.extend(
                journal_silent_handlers(
                    path.read_text(encoding="utf-8"), path.as_posix()
                )
            )
        except SyntaxError:
            continue
    return sorted(rows, key=lambda r: (r["file"], r["line"]))


def classify_file(path: Path) -> list[dict]:
    return classify_source(
        path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix()
    )


#: Every production root (as in INV-2's reachability list); a narrower scope makes the zero lie.
_AUDIT_ROOTS: tuple[str, ...] = ("core", "cli", "app", "api", "tools")


def audit() -> list[dict]:
    rows: list[dict] = []
    # rglob, so a future subdirectory does not fall outside the ratchet.
    for root in _AUDIT_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
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
    print(f"broad except handlers in production roots (core/cli/app/api/tools): {len(rows)}")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind}: {count}")
    print(f"silent WITHOUT a stated reason (audit target): {len(bad)}")
    per_file = Counter(r["file"] for r in bad)
    for file, count in per_file.most_common():
        print(f"  {file}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
