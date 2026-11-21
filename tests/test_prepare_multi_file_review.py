"""Direct pins for the multi-file review decision (loop decomposition, piece 6).

Until this move the decision was reachable only through a fully built agent —
covered by tests/test_operator_file_evidence.py's integration flows, which
stay authoritative for end-to-end behaviour. These tests pin the decision
table itself at its new seam: none / refusal / forced, and the guard that a
work order must never be swallowed by a reading.
"""
from __future__ import annotations

from pathlib import Path

from core.file_request_intent import (
    force_file_hint_read_when_explicit,
    prepare_multi_file_review,
)
from core.planner import PlannerOutput

_LOGGED: list[tuple[str, dict]] = []


def _log(event: str, payload: dict) -> None:
    _LOGGED.append((event, payload))


def test_the_modules_runtime_imports_stay_inside_the_boundary():
    """The module docstring promises "No LLM" — Copilot caught a runtime
    `PlannerOutput` import pulling `core.llm` in transitively.

    Pinned statically rather than by spawning an interpreter: the AST of the
    module is read and every top-level runtime import (anything outside an
    `if TYPE_CHECKING:` block) must come from an allowlist. This is stricter
    than the leak it fixes — ANY new runtime dependency fails, not only the
    LLM stack — and an in-process runtime probe would lie anyway, because
    other tests have usually imported the stack before this one runs.
    """
    import ast

    allowed = {"re", "dataclasses", "pathlib", "typing", "tools.file_read"}
    # Anchored to this file, not the CWD — Copilot's point about the previous
    # subprocess probe applied equally here: a pytest run from outside the
    # repo root (or a leaked chdir) must not turn into a false failure.
    module_path = (
        Path(__file__).resolve().parent.parent / "core" / "file_request_intent.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    runtime_imports: set[str] = set()
    for node in tree.body:  # top level only: guarded blocks are not walked
        if isinstance(node, ast.Import):
            runtime_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            runtime_imports.add(node.module or "")

    off_limits = runtime_imports - allowed - {"__future__"}
    assert not off_limits, (
        f"new runtime imports outside the declared boundary: {sorted(off_limits)}"
        " — the module promises deterministic, no-LLM behaviour; type-only"
        " imports belong under `if TYPE_CHECKING:`"
    )


# ---------------------------------------------------------------------------
# The decision table
# ---------------------------------------------------------------------------


def test_no_workspace_root_refuses_rather_than_guessing(tmp_path):
    verdict = prepare_multi_file_review(
        "сравни a.md и b.md", file_hint=None,
        workspace_root=None, log=_log,
    )
    assert verdict["kind"] == "refusal"


# ---------------------------------------------------------------------------
# force_file_hint_read_when_explicit
# ---------------------------------------------------------------------------

def _planner_out(sources=()):
    return PlannerOutput(
        reasoning="r", sources=list(sources), raw_response="{}", warnings=[],
    )


def test_a_non_explicit_question_is_left_alone():
    out = force_file_hint_read_when_explicit(
        _planner_out(), question="какая сегодня погода", file_hint="task.md",
    )
    assert out.sources == []
