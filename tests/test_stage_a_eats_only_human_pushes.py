"""Stage A is fed by human TODO comments — the push the charter was built to end.

Operator, 2026-08-19: «мы пытаемся реализовать автономного Агента, а не
давать ему пинки под зад и писать какие-то ему задачи; он сам должен
выбирать задачи». He is right, and the code agrees with him: the backlog
today holds 12 SELF-measured candidates (oversized modules, found by the
agent applying its own size rule to its own tree) and 1 code_todo — a
comment an engineer typed. Stage A's default selector walks that backlog
and accepts ONLY the code_todo, ignoring all twelve.

So the organ that writes coding tasks cannot start without a human first
writing a comment into the source. That is exactly the mechanism
core/charter_goal.py exists to replace («его толкают и дают что-то
делать», operator 2026-08-15) — removed at the goal layer, still load-
bearing one floor down.

Banked, not fixed: whether Stage A should read self-measured signals is a
design decision for the operator, and a wrong turn here would let the
agent open coding tasks against its own whole tree. The green test pins
today's truth so the decision is made on measurement, not memory.
"""
from __future__ import annotations

from core.backlog_selector import load_backlog
from core.self_task_producer import _default_task_selector

_REPO = "."


def test_the_backlog_is_mostly_self_measured() -> None:
    """The material for autonomous work exists — it is simply not eaten."""
    sources = [str(getattr(c, "signal_source", "")) for c in load_backlog(_REPO)]
    assert sources, "backlog is empty — this test measures nothing"
    self_found = [s for s in sources if s != "code_todo"]
    assert self_found, (
        "no self-measured candidates at all: the premise of the finding is gone"
    )


def test_stage_a_accepts_only_human_written_todos() -> None:
    """THE FINDING: the selector takes a code_todo or returns nothing, whatever
    else the backlog holds. When this stops being true — because the operator
    decided to widen it — this test fails and the docstring above is the
    record of why it was ever so."""
    candidates = list(load_backlog(_REPO))
    picked = _default_task_selector(_REPO)()
    non_todo = [c for c in candidates if str(getattr(c, "signal_source", "")) != "code_todo"]

    if picked is None:
        assert non_todo, (
            "selector picked nothing AND the backlog has no self-measured "
            "candidates — inconclusive"
        )
        return
    assert str(getattr(picked, "signal_source", "")) == "code_todo", (
        "selector picked a non-code_todo candidate — the push dependency is "
        "gone and this bank should be replaced by the new contract"
    )
