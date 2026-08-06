"""Repair picks its model by the job's size, not by a fixed config line.

`propose_repair` built its model with `model_router.for_role(REPAIR_PROPOSAL)` —
one line, and the reason the hardest work in the system ran on whatever the role
config named. Measured: `gpt-4o-mini` was handed a 728-line rewrite and returned
the file unchanged.

`for_role` cannot escalate. `for_task` can, and it routes the request through the
same operator gate everything else uses, so wiring repair into it does not widen
the agent's authority — it only lets repair *ask*.

## Why complexity is measured here, not read from the prompt

`assess_complexity` scans prose for signals like "архитектур", "audit", "from
scratch". A repair request reads `repair core/loop_methods2.py so the failing
tests pass` — no signal, so it would always assess STANDARD no matter how large
the file. Difficulty for a repair is an objective property of the work: how much
code must be reproduced, and how many tests are red. That is computed and passed
as `force_tier`, which the router still runs through the escalation gate (its
docstring: "a forced DEEP still passes through the operator-escalation check").
"""
from __future__ import annotations

from pathlib import Path

from core.task_complexity import ComplexityTier


def _tier(**kwargs) -> ComplexityTier:
    from core.repair_proposal import repair_complexity

    return repair_complexity(**kwargs)


# ── the size of the job decides ──────────────────────────────────────────────

def test_a_small_file_is_ordinary_work():
    assert _tier(target_chars=1_500, failing_tests=1) is ComplexityTier.STANDARD


def test_a_large_rewrite_is_deep_work():
    """The MIR-052 case: 728 lines to reproduce exactly, with an edit inside."""
    assert _tier(target_chars=36_959, failing_tests=2) is ComplexityTier.DEEP


def test_many_failing_tests_make_a_mid_sized_file_deep():
    """Several red tests at once means the change is not a one-liner."""
    assert _tier(target_chars=9_000, failing_tests=1) is ComplexityTier.STANDARD
    assert _tier(target_chars=9_000, failing_tests=5) is ComplexityTier.DEEP


def test_a_trivial_target_never_asks_for_the_expensive_model():
    for chars in (0, 200, 900):
        assert _tier(target_chars=chars, failing_tests=1) is not ComplexityTier.DEEP


# ── and the gate still decides whether asking gets anything ──────────────────

def test_deep_work_downgrades_when_the_operator_funded_nothing(monkeypatch, tmp_path):
    """Off by default: the assessment says DEEP, the budget says no, cheap wins.

    This is the property that makes the whole change safe to merge — with no
    env var set, an autonomous repair behaves exactly as it did before.
    """
    monkeypatch.delenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", raising=False)

    from core.deep_escalation import (
        DeepEscalationRequest,
        deep_budget_ok,
        deep_call_budget,
        evaluate_deep_escalation,
    )

    limit = deep_call_budget()
    decision = evaluate_deep_escalation(DeepEscalationRequest(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        deep_model_available=True,
        budget_ok=deep_budget_ok(None, limit=limit),
        operator_approved=False,
    ))

    assert limit == 0
    assert decision.downgraded
    assert "budget_block" in decision.route_reason


def test_deep_work_is_allowed_once_the_operator_funds_it(monkeypatch):
    monkeypatch.setenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", "2")

    from core.deep_escalation import (
        DeepEscalationRequest,
        deep_budget_ok,
        deep_call_budget,
        evaluate_deep_escalation,
    )

    decision = evaluate_deep_escalation(DeepEscalationRequest(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        deep_model_available=True,
        budget_ok=deep_budget_ok(None, limit=deep_call_budget()),
        operator_approved=False,
    ))

    assert decision.approved, decision.route_reason


# ── the failing-test signal must actually reach the decision ─────────────────
#
# The first version of this wiring passed `failing_tests=0` as a literal, so the
# "many red tests → deep" half of `repair_complexity` could never fire in the
# real path — the logic existed, the tests covered it, the docstring described
# it, and production always got zero. Caught in review by Aikido.
#
# The count is only knowable *after* the baseline run, which happens inside
# `generate()`, while the model was being built before it. So the generator now
# asks for its model once the baseline is in, and these tests pin that the
# question carries the real number.


def test_the_generator_asks_for_its_model_after_the_baseline(tmp_path):
    """The selector is called with the count the baseline actually produced."""
    from core.repair_proposal import RepairProposalGenerator

    (tmp_path / "target.py").write_text("x = 1\n", encoding="utf-8")
    asked: list[int] = []

    class _Recorder:
        def complete(self, **kwargs):
            return '{"diagnosis": "d", "target_file": "target.py", ' \
                   '"proposed_content": "x = 2\\n", "evidence": [], "confidence": 0.5}'

    class _RunTests:
        def run(self, **kwargs):
            return {"exit_code": 1, "passed": 1, "failed": 3, "errors": 0,
                    "timed_out": False, "skipped": 0, "total": 4}

        def validate_output(self, output):
            return True, []

    def _selector(failing_tests: int):
        asked.append(failing_tests)
        return _Recorder()

    generator = RepairProposalGenerator(
        workspace_root=tmp_path, llm=_Recorder(), llm_selector=_selector
    )
    generator.run_tests = _RunTests()
    generator.generate(target_path="target.py")

    assert asked == [3], (
        f"the model was chosen with {asked} failing tests, not the 3 the "
        f"baseline reported"
    )


def _propose_repair_source() -> str:
    """The body that DECIDES, found through the object rather than a path.

    Two moves brought it here. C1 replaced `Path("core/loop_repair.py")` with
    `inspect.getsource`, because reading shipped code by path reddens a test
    about something else the moment the file moves. B1 then moved the logic out
    of the mixin into `core/repair_commands.py` — and because the lookup already
    followed the object, following it was a one-line change of WHICH object,
    not a path edit. That is the whole value C1 bought.

    Slicing between `def propose_repair(` and `def repair(` is gone with it: a
    method added between those two `def`s would have silently widened what these
    guards read.
    """
    import inspect

    from core.repair_commands import propose_repair

    return inspect.getsource(propose_repair)


def _facade_source() -> str:
    """The mixin method, which must stay thin."""
    import inspect

    from core.loop_repair import AgentLoopRepair

    return inspect.getsource(AgentLoopRepair.propose_repair)


def test_the_mixin_is_a_facade_and_not_the_home_of_the_decision():
    """B1's invariant: `agent.` stays the entry point, not the owner.

    The operator's ruling was neither "move the file" nor "rewrite the CLI":
    `agent.propose_repair(...)` still works and no call site changed, but the
    mixin must stop being where the decision lives. Checking both halves,
    because either alone would pass while the point was lost.
    """
    facade = _facade_source()

    # By CALL, not by the alias spelling: the import alias is an implementation
    # detail (`repair_ops` today, because the dotted form is what the
    # architecture invariant can see), and a guard keyed on it would redden on a
    # rename that changes nothing.
    import ast

    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(facade.strip()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "propose_repair" in calls, (
        "the facade no longer delegates — the entry point has been broken"
    )
    for owned_elsewhere in ("for_task", "force_tier", "repair_complexity",
                            "OperatorEscalation", "llm_selector"):
        assert owned_elsewhere not in facade, (
            f"{owned_elsewhere!r} came back into the mixin; the decision belongs "
            "to core/repair_commands.py"
        )


def test_the_subsystem_takes_its_dependencies_as_arguments():
    """Not an agent it reaches into.

    A function taking `agent` and pulling `model_router` off it with `getattr`
    would have moved the coupling rather than removed it — the duck-typed seam
    the census already found where `core/ingestion.py` looks up
    `_remember_from_knowledge` by name.
    """
    import inspect

    from core.repair_commands import propose_repair

    params = set(inspect.signature(propose_repair).parameters)

    assert {"model_router", "log"} <= params
    assert "agent" not in params
    assert "self" not in params


def test_the_wiring_does_not_hardcode_the_failing_count():
    """Source-level guard: the literal that made this unreachable is banned."""
    body = _propose_repair_source()

    assert "failing_tests=0" not in body, (
        "the failing-test signal is pinned to zero again, so the count can "
        "never influence the tier no matter what the baseline reports"
    )


# ── the wiring itself ────────────────────────────────────────────────────────

def test_propose_repair_no_longer_uses_the_unescalatable_path():
    """`for_role` cannot reach deep at all — repair must not be built on it."""
    body = _propose_repair_source()

    # The model that answers the request must come from `for_task`, chosen by a
    # selector once the baseline is known. A `for_role` call may still appear as
    # the placeholder for paths that never reach the model at all (unreadable
    # target, already-green tests) — so the invariant is about how the *request*
    # is routed, not about the word being absent.
    assert "model_router.for_task(" in body, (
        "propose_repair does not route its request through for_task, which is "
        "the only path that can escalate"
    )
    assert "llm_selector=" in body, (
        "the model is fixed before the baseline runs, so the failing-test half "
        "of the difficulty signal cannot reach the decision"
    )


def test_propose_repair_asks_with_a_concrete_deliverable():
    """A vague ask downgrades by design, so the wiring must name what it wants."""
    body = _propose_repair_source()

    assert "high_value_repair" in body
    assert "minimal_patch_plan" in body
    assert "operator_approved=False" in body, (
        "the autonomous path must not mark itself operator-approved — that "
        "would bypass the budget check entirely"
    )
