"""A substituted subject must not be recorded as the original subject.

Operator prerequisite before the autonomous agent is switched on again
(2026-08-20), stated so that it chooses no policy:

    Diagnosis-targeted learning may not be recorded as successfully grounded
    in the agent's own weak spots when the named diagnosis targets were not
    resolved into real sources.

The fallback may stay. What may not stay is a record in which studying ten
default files is indistinguishable from studying the thing that was
diagnosed. MIR-106 measured that exact shape live: 10 of 12 named weak spots
did not exist, two of four plans contained none of their named targets, and
the journal still read `sources=10, claims=80`.

This pins the distinction, not the remedy. Rejecting the plan, dropping the
phantom targets, or raising a phantom as its own defect signal all remain
open — each is a different policy about what the agent should do when its
self-diagnosis names something that is not there.
"""
from __future__ import annotations

from pathlib import Path

from core.reflection import learning_grounding

_REPO = Path(__file__).resolve().parents[1]


def test_a_resolved_target_is_recorded_as_targeted() -> None:
    g = learning_grounding(
        focus_areas=["tools/file_read.py"],
        source_paths=["tools/file_read.py", "core/architecture_audit.py"],
        workspace=_REPO,
    )
    assert g["status"] == "targeted"
    assert g["resolved"] == ["tools/file_read.py"]
    assert g["phantom"] == []


def test_a_phantom_target_is_never_recorded_as_targeted() -> None:
    g = learning_grounding(
        focus_areas=["core/reasoning.py", "core/citation.py"],
        source_paths=["core/verifier.py", "tests/test_verifier.py"],
        workspace=_REPO,
    )
    assert g["status"] == "invalid_diagnosis", (
        "a plan whose named weak spots do not exist was recorded as "
        f"{g['status']!r} — the substitution is invisible again"
    )
    assert sorted(g["phantom"]) == ["core/citation.py", "core/reasoning.py"]
    assert g["resolved"] == []


def test_a_real_target_that_the_plan_dropped_is_not_targeted() -> None:
    """The subtler half: the file exists, but the plan studied other things."""
    g = learning_grounding(
        focus_areas=["tools/file_read.py"],
        source_paths=["core/verifier.py"],
        workspace=_REPO,
    )
    assert g["status"] == "substituted"
    assert g["resolved"] == []


def test_a_topic_that_is_not_a_path_is_recorded_as_unresolvable() -> None:
    """The residual live case. `_checked_focus_area` has refused path-shaped
    phantoms since 2026-08-15, but a topic name cannot be checked against disk
    at all — and the planner answers it with defaults. It must not be recorded
    as targeted study of that topic."""
    g = learning_grounding(
        focus_areas=["memory subsystem"],
        source_paths=["core/verifier.py", "tests/test_verifier.py"],
        workspace=_REPO,
    )
    assert g["status"] == "unresolvable"
    assert g["unresolvable"] == ["memory subsystem"]
    assert g["resolved"] == []


def test_a_mixed_plan_is_partial() -> None:
    g = learning_grounding(
        focus_areas=["tools/file_read.py", "core/reasoning.py"],
        source_paths=["tools/file_read.py", "core/verifier.py"],
        workspace=_REPO,
    )
    assert g["status"] == "partial"
    assert g["resolved"] == ["tools/file_read.py"]
    assert g["phantom"] == ["core/reasoning.py"]
