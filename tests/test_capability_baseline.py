"""The capability bench must keep running, and must not quietly get easier.

Operator instruction 2026-08-05: turn the fix-direction argument into a
measurable experiment. A bench nobody runs decides nothing, and a bench whose
tasks drift decides the wrong thing — so this pins the shape and the score.

The numbers here are a FLOOR, not a target. They are what the system reached
before any fix, recorded so a later change can be shown to have moved them.
Raise them when a fix lands and the run proves it; never lower them to make a
run pass.
"""
from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

from tests.capability_tasks import ALL_TASKS, CATEGORIES

_REPO = Path(__file__).resolve().parents[1]

#: Measured 2026-08-05.
#:   verdict — did the system reach the right yes/no
#:   reason  — did it produce a sentence the agent could repair itself from
#:
#: 26 / 13 before MIR-060 direction (b); 29 / 17 after. Raised deliberately,
#: with the run that earned it. A floor, never a target: lowering it to make a
#: run pass would turn the bench into a formality.
_BASELINE_VERDICT = 29
_BASELINE_REASON = 17


def _harness():
    path = _REPO / "scripts" / "capability_baseline.py"
    spec = importlib.util.spec_from_file_location("capability_baseline", path)
    assert spec is not None and spec.loader is not None, f"стенд не загружается: {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score() -> tuple[int, int]:
    harness = _harness()
    verdict = reason = 0
    for task in ALL_TASKS:
        ok, gave_reason, _ = harness._RUNNERS[task.category](task)
        verdict += ok
        reason += gave_reason
    return verdict, reason


def test_the_bench_has_the_shape_the_operator_asked_for():
    """20-50 tasks across the five named categories, none of them empty."""
    assert 20 <= len(ALL_TASKS) <= 50, len(ALL_TASKS)
    per_category = Counter(t.category for t in ALL_TASKS)
    assert set(per_category) == set(CATEGORIES), sorted(per_category)
    for category in CATEGORIES:
        assert per_category[category] >= 5, (category, per_category[category])


def test_every_task_states_the_reason_a_repair_would_need():
    """`reason_needed` is the whole point — a task without one scores nothing.

    The operator's criterion is that a label change is not a capability gain.
    A task that cannot say what the agent would have to be told in order to fix
    its reasoning cannot distinguish the two, so an empty one is a bench defect.
    """
    empty = [t.id for t in ALL_TASKS if not t.reason_needed.strip()]
    assert not empty, f"задачи без нужной причины: {empty}"


def test_both_answers_appear_in_every_claim_category():
    """A category of only-false claims is passed by a system that always says no."""
    for category in ("inference", "arithmetic", "contradiction"):
        holds = {t.holds for t in ALL_TASKS if t.category == category}
        assert holds == {True, False}, (category, holds)


def test_the_score_does_not_fall_below_the_recorded_baseline():
    verdict, reason = _score()
    assert verdict >= _BASELINE_VERDICT, (
        f"вердиктов {verdict} < базы {_BASELINE_VERDICT} — способность упала"
    )
    assert reason >= _BASELINE_REASON, (
        f"причин {reason} < базы {_BASELINE_REASON} — стало меньше объяснений"
    )


_PAIRS = "alpha=1" + chr(10) + "beta=2" + chr(10) + "gamma=3" + chr(10)


def test_the_verifier_now_explains_an_arithmetic_refutation():
    """The finding that decided the fork, and its repair.

    Before direction (b) this test asserted the opposite: `ClaimChunk` was
    `(text, citations, matched_evidence_ids, verdict)` with no field a reason
    could live in, so the reason column was exactly zero across all 24 claim
    tasks — and no choice among the three fix directions could have changed it
    without changing the data structure. That was the measurement that made
    the argument decidable.

    It now checks the other direction: a refuted arithmetic claim carries the
    computed value, the claimed value and the numbers behind them, so the agent
    is told what to change rather than merely that it was wrong.
    """
    from core.claim_arithmetic import evaluate

    verdict = evaluate("The three values sum to 99", _PAIRS)

    assert verdict.refutes
    assert verdict.expected == "6" and verdict.actual == "99"
    assert "6" in verdict.explanation and "99" in verdict.explanation
    assert "alpha=1" in verdict.computed_from, (
        "опровержение без выкладки — это штамп, а не причина"
    )


def test_a_shape_it_does_not_recognise_stays_silent():
    """Silent, never `refutes` — a false accusation is the unrecoverable error."""
    from core.claim_arithmetic import evaluate

    assert evaluate("The configuration looks reasonable", _PAIRS).outcome == "silent"
    assert evaluate("The three values sum to 3", "prose, not pairs").outcome == "silent"
