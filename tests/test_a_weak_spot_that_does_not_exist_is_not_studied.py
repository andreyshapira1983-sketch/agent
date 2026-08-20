"""Banked: reflection names a weak spot, and nothing checks that it exists.

Measured on live traffic 2026-08-20. Reflection ran seven times and produced
four learning plans. Of the twelve weak spots its lessons named, **ten do not
exist as files**: core/reasoning.py, core/citation.py, core/user_contract.py,
core/obligation_management.py, core/logical_coherence.py,
core/file_management.py and others — plausible module names the model
invented. Only tools/file_read.py and core/__init__.py were real.

What that costs, from the same logs:

  named tools/file_read.py (real)   -> it is the plan's first source
  named core/__init__.py   (real)   -> it is the plan's first source
  named four phantoms               -> NONE of them enters the plan, which
                                       silently returns ten default sources,
                                       and the run logs
                                       reflection_learning_ingest sources=10
                                       claims=80 — a success

So the edge carries its signal only when the model happens to name a real
path. When it does not, the agent studies files unrelated to any diagnosed
defect while the journal says it studied its weak areas. The architecture
ascribes "identify the weak spot, then study it"; the mechanism performs
that in 2 cases of 12.

The invariant below is the smallest one that would have caught it. The fix is
deliberately unprescribed: refuse the plan, drop the phantom focus areas, or
record them as a defect signal of their own — each is a different policy.
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


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured live 2026-08-20 and banked rather than fixed "
        "(MIR-106): 10 of the 12 weak spots reflection named do not exist, and "
        "the planner answers a goal full of phantoms with ten default sources "
        "instead of refusing. The invariant: a plan built to study a named "
        "weak spot must not silently study something else. Fix unprescribed — "
        "refuse, drop the phantom, or raise it as its own defect signal."
    ),
    strict=True,
)
def test_a_phantom_weak_spot_does_not_yield_a_confident_plan() -> None:
    phantoms = ["core/reasoning.py", "core/citation.py", "core/user_contract.py"]
    assert not any((_REPO / p).is_file() for p in phantoms), (
        "these were phantom paths when this bank was written; if one now "
        "exists, re-measure before trusting the bank"
    )
    plan = _plan_for(phantoms, _REPO)
    assert not list(plan.source_paths), (
        "a goal naming only files that do not exist produced "
        f"{len(list(plan.source_paths))} sources to study"
    )
