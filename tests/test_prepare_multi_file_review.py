"""Direct pins for the multi-file review decision (loop decomposition, piece 6).

Until this move the decision was reachable only through a fully built agent —
covered by tests/test_operator_file_evidence.py's integration flows, which
stay authoritative for end-to-end behaviour. These tests pin the decision
table itself at its new seam: none / refusal / forced, and the guard that a
work order must never be swallowed by a reading.
"""
from __future__ import annotations

from core.file_request_intent import (
    force_file_hint_read_when_explicit,
    prepare_multi_file_review,
)
from core.planner import PlannerOutput

_LOGGED: list[tuple[str, dict]] = []


def _log(event: str, payload: dict) -> None:
    _LOGGED.append((event, payload))


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
