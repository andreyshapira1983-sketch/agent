"""A banked procedure must record what the run was for, not just which tools ran.

Measured on the live agent: five rounds produced procedures named
``"Workflow using shell_exec, shell_exec"`` with steps ``"Run tool: shell_exec"``
twice. That is the shape of a run. It carries no information about what the
workflow was ever good for — not for a human reader, and not for the retrieval
search, which scores on name/tags/steps and therefore scored zero against any
query about the subject.

The boundary the operator set for this change is the second half of this file:
**what** is stored changes; **when** a procedure is created does not. Widening
admission would bank better-written lessons behind the same false successes.
"""
from __future__ import annotations

from core.smart_memory import (
    episode_from_agent_cycle,
    procedure_from_episode,
)


def _episode(**kw):
    base = {
        "goal": "Answer the question: where is the output lost",
        "question": "Where is the shell_exec output lost on the way to the synthesizer?",
        "answer": "a",
        "tools_used": ["shell_exec", "file_read"],
        "source_labels": ["shell_exec:grep -n", "file:core/loop.py"],
        "verified_chunks": 8,
        "unverified_chunks": 1,
        "declared_completion": "achieved",
    }
    base.update(kw)
    return episode_from_agent_cycle(**base)


# ---------------------------------------------------------------------------
# The lesson is assembled from what was observed, and nothing else
# ---------------------------------------------------------------------------


def test_a_procedure_without_material_stores_no_lesson():
    procedure = procedure_from_episode(_episode(question=""))
    assert procedure is not None
    assert procedure.lessons == ()


# ---------------------------------------------------------------------------
# The record says what it was for
# ---------------------------------------------------------------------------


def test_a_run_without_a_recorded_question_still_yields_a_usable_record():
    procedure = procedure_from_episode(_episode(question=""))
    assert procedure.steps[0] == "Situation: (not recorded)"
    assert procedure.name.startswith("Workflow using")


# ---------------------------------------------------------------------------
# Accumulation — the known key-pooling defect must stay visible
# ---------------------------------------------------------------------------


def test_an_uncredited_episode_contributes_no_lesson():
    """No credit, no lesson — the two move together."""
    credited = procedure_from_episode(_episode())
    blocked = credited.merged_from_episode(
        _episode(
            defect_signals=["obligation_silently_missing"],
            declared_completion="achieved",
        )
    )
    assert blocked.lessons == credited.lessons
