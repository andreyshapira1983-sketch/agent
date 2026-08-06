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
    # Leading underscores stripped BEFORE matching: a private journaling
    # helper is journaling. `self._log(...)` used to score as silence, because
    # the token was `_log` and the match is exact — so five handlers in
    # `core/autonomous_runtime.py` that do report their failure were counted
    # in the target class, and the audit sent a reader to fix what was not
    # broken. Measured 2026-08-05: 46 flagged, 5 of them journaling this way.
    #
    # Stripping does not widen the match: `_login`/`_logic` become
    # `login`/`logic`, neither of which is in `_LOGGY`, so the token rule from
    # review round #292 still holds — `login()` is not journaling.
    parts = [p.lstrip("_") for p in name.lower().split(".")]
    # `log_error` / `log_summary` are journaling too, and both exist in `core/`.
    # The prefix is `log_` WITH the separator on purpose: it admits those and
    # still refuses `login` and `logic`, which is the whole point of #292.
    return any(k in parts for k in _LOGGY) or any(p.startswith("log_") for p in parts)


#: Helpers that journal on the caller's behalf. A handler calling one of these
#: DOES report; scoring it silent sends a reader to fix what is not broken.
#:
#: `_sensor_failed` is the loop layer's own answer to "an observer must not
#: break the turn, and must not vanish either" — it delegates to
#: `core/sensor_journal.py`. The audit matched a literal `.log(` call and so
#: counted 15 reporting handlers as silent (measured 2026-08-05), which is how
#: a handler that reports correctly ended up having to carry a justifying
#: comment to satisfy this very tool.
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


def _unconditional_reporters(tree: ast.AST) -> frozenset[str]:
    """Functions in this file that ALWAYS write to the journal when called.

    "Always" is doing the work. A helper that logs only inside an `if` reports
    on some paths and not others, so a handler delegating to it has not
    necessarily reported anything — counting it would let a real silence hide
    behind a conditional. A write inside a `try` body still counts: `try` is not
    a branch, it runs.

    Nested function definitions are skipped: a closure's log call belongs to the
    closure, and the outer function may never call it.
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

    A different question from `classify_source`, which asks whether a silent
    handler carries a COMMENT. A comment helps whoever reads the code; it does
    nothing for an operator reading logs while the agent runs unattended. The
    census (2026-08-05) traced three consequences of that gap — an answer-safety
    check whose failure looked like success, a referent resolver that could stop
    working unnoticed, a task contract silently replaced — and none of the three
    handlers was uncommented.

    Two exclusions, both structural rather than a matter of taste:

    * a handler that RE-RAISES has reported by the strongest means available;
    * a handler nested inside another handler that reports is the last-resort
      guard around reporting itself, and reporting from inside a failed report
      is a recursion, not a fix.

    Counted here rather than in a throwaway script because two ad-hoc passes
    over the same layer produced 22 and 18. One implementation, one number.
    """
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    local_reporters = _unconditional_reporters(tree)

    def _reports(handler: ast.ExceptHandler) -> bool:
        for n in ast.walk(handler):
            if _is_reporting_call(n):
                return True
            # A handler that delegates its report to a helper HAS reported. The
            # rule used to be a literal `.log(` inside the handler, which scored
            # `_enforce_answer_safety` silent while it was calling
            # `_safe_answer_after_enforcement_failure`, whose first statement is
            # the `answer_enforcement_failed` write. Punishing code for moving a
            # report into a helper is the counter's error, not the code's.
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in local_reporters
            ):
                return True
        return False

    # A `try` whose body is nothing but journal writes: the handler guards the
    # act of reporting, and reporting a failed report is a recursion. Identical
    # in kind to the nested-handler exclusion below and to `try_only_logs` in
    # `classify_source` — which is where this rule already lived. It was written
    # once and not carried across, so `core/loop_run_tail.py:155` counted as a
    # silence the layer had no way to remove.
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
    """The loop layer, INCLUDING subsystems extracted out of it.

    A scope written as "files named ``loop*``" measures where someone looked
    rather than where the defect can be, and census item B1 proved that costs
    something real: moving `propose_repair` into `core/repair_commands.py` moved
    one journal-silent handler with it, the budget read one lower, and nothing
    had been fixed. A refactor must never be able to look like a repair.

    So the scope follows the code. A mixin that keeps a thin facade and delegates
    to its subsystem imports it as ``import core.X as Y`` — the dotted form the
    architecture invariant can see — and that import is what pulls X back into
    the measurement. Anything the layer extracts this way stays counted.

    Not a general import walk on purpose: `from core.X import f` pulls in
    helpers the layer merely USES, and counting those would claim past what the
    census measured. The dotted-alias form is what the facade pattern uses, and
    a future extraction that hides from this by choosing the other form still
    has to get past `test_the_budget_matches_the_measurement`, which goes red on
    a count that drops for any reason at all.
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
