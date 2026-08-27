"""Reflection names a weak spot; can the agent tell whether it is real?

Measured on live traffic 2026-08-20, then **re-measured after the first
reading turned out to be wrong** — the correction is the useful part.

What the logs show: of the twelve weak spots reflection's lessons named, ten
do not exist as files — core/reasoning.py, core/citation.py,
core/user_contract.py and others, plausible module names the model invented.
Only tools/file_read.py and core/__init__.py were real.

What that does NOT show: that the current tree accepts them. Every one of the
ten is from 2026-08-15, the day commit `010bdd2` added `_checked_focus_area`
to core/reflection.py — "a lesson may not point at a file that does not
exist". The first test below proves by intervention that a path-shaped
phantom is now blanked and its `repair` intent downgraded to `monitor`.
Historical logs, current code: a different question.

The residual is narrower and still live. The guard can only question a name
that LOOKS like a path. `"memory subsystem"` is unresolvable by shape, passes
through untouched, and reaches the planner — which answers it with very
nearly the default source list. That is what the strict xfail banks.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _plan_for(focus_areas: list[str], workspace: Path):
    from core.learning_planner import LearningPlanner

    goal = "reflection: study weak areas — " + ", ".join(focus_areas)
    return LearningPlanner().plan(workspace=workspace, goal=goal, limit=5)


def test_a_real_weak_spot_reaches_the_plan() -> None:
    """Boundary pin: when the name is real the signal does cross."""
    plan = _plan_for(["tools/file_read.py"], _REPO)
    assert "tools/file_read.py" in list(plan.source_paths)


def test_a_path_shaped_phantom_is_refused_before_it_becomes_a_goal() -> None:
    """The half that is fixed, proven at the guard rather than by reading it."""
    from core.reflection import _checked_focus_area

    assert not (_REPO / "core/reasoning.py").is_file(), (
        "core/reasoning.py exists now — re-measure before trusting this test"
    )
    assert _checked_focus_area("core/reasoning.py", "repair") == ("", "monitor")
    assert _checked_focus_area("tools/file_read.py", "repair") == (
        "tools/file_read.py",
        "repair",
    )


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, re-measured 2026-08-20 and banked rather than fixed "
        "(MIR-106): a topic-shaped weak spot cannot be checked against disk, "
        "and naming one changes nothing — 'planner interface design' returns "
        "the same five sources as naming no weak spot at all, and as a "
        "nonsense topic. Recorded honestly since today (learning_grounding -> "
        "'unresolvable'), but no policy chosen: refuse, study anyway and say "
        "so, or treat an unresolvable self-diagnosis as its own defect signal. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_naming_a_topic_weak_spot_changes_what_is_studied() -> None:
    bare = list(_plan_for([], _REPO).source_paths)
    topic = list(_plan_for(["planner interface design"], _REPO).source_paths)
    nonsense = list(_plan_for(["ZZZQQQ nonexistent topic 8811"], _REPO).source_paths)
    assert topic != bare, (
        f"naming a weak spot studied exactly what naming nothing studies: {bare}"
    )
    assert topic != nonsense, (
        f"a topic and gibberish produced the same study plan: {topic}"
    )
