"""The in-run Goal and Plan end the run with a verdict — MIR-026.

The ephemeral Goal/Plan are LOG objects: their whole purpose is to let a
journal reader reconstruct the run. Born `pending` / `in_progress` and never
finalised, they made a finished run and an abandoned one indistinguishable by
their own objects (LPF-016). Settled at the attempt loop's single exit:
`done` when the loop broke with its work, `failed` on replan exhaustion — the
one failure the loop itself can declare. Cosmetic severity, honest journal.
"""
from __future__ import annotations

from pathlib import Path


def test_a_budget_interruption_settles_too(workspace: Path) -> None:
    """Audit of this closure (CLOSURE_AUDIT_2026-08-22.md in git history).

    The field's rule for settle-on-exit is that a status written at ONE exit
    lies about the others. Probed by parsing the attempt loop: it has no
    `return`, but it does re-raise `ModelBudgetExceeded` after saving a pause
    checkpoint — an exit that skipped the settle, leaving the objects
    `pending`, which in this vocabulary means «never started» rather than
    «interrupted». Both exits settle now.
    """
    import ast
    import inspect

    import core.loop_attempt as mod

    tree = ast.parse(inspect.getsource(mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_attempt_loop")
    settle_calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "_settle_run_objects"
    ]
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]

    assert len(settle_calls) >= 1 + len(raises), (
        f"{len(raises)} escaping raise(s) but only {len(settle_calls)} settle "
        "call(s) — an exit leaves the log objects reading «never started»"
    )
