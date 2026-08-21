"""Proofs 2, 3 and 6 of the consolidation set — a run's rights are its own.

Three invariants, measured before they were written. The original list had
eight; measurement dissolved three of them and this file says why rather than
keeping them alive for the sake of the number:

  1  "the same AgentLoop performs two organ-runs sequentially" is not an
     invariant but a precondition of 2, and it is asserted there.
  4  "self-build does not get hygiene authority" and
  5  "hygiene does not get self-build rights" rest on a premise that proof 8
     disproved: the four organs are built with identical arguments and hold one
     envelope, so there is no per-organ authority to keep apart. What can leak
     is a RUN's temporary narrowing, and that is invariant 3.

So five real invariants remain, not eight. Inflating a count is the same error
as inflating a claim.

The probe used throughout is `PolicyGate.check` against tools that are
REGISTERED, so a deny has exactly one possible cause. The stubs never execute.
"""
from __future__ import annotations

from pathlib import Path

from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.models import Action
from tests.test_dry_run_learning_state import _durable_agent
from tools.base import Tool


class _Stub(Tool):
    """Registered, read-only, never executed — so the gate has one reason."""

    description = "probe stub: registered so a deny can only be the block"
    risk = "read_only"

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, **kwargs):  # pragma: no cover - never invoked
        raise AssertionError(f"the probe stub {self.name!r} must never run")


class _Done(Exception):
    """Ends the fake run once its observation is taken."""


def _agent(workspace: Path):
    agent = _durable_agent(workspace)
    for name in ("rss_fetch", "run_tests", "web_search"):
        agent.registry.register(_Stub(name))
    return agent


def _verdict(agent, tool: str) -> str:
    return agent.policy.check(
        Action(step_id="probe", type="tool_call", tool_name=tool, parameters={})
    ).decision


def _observe_during_run(agent, workspace: Path, observe, **config_kw) -> object:
    """Run one goal task and take a reading from inside its narrowed window."""
    box: dict[str, object] = {}

    def fake_run(*_a, **_k):
        box["seen"] = observe()
        raise _Done()

    agent.run = fake_run  # type: ignore[method-assign]
    runtime = AutonomousRuntime(agent, workspace=workspace)
    try:
        runtime._task_goal(
            AutonomousTask("goal", "probe"),
            AutonomousRuntimeConfig(goal="probe", limit=1, **config_kw),
        )
    except _Done:
        pass
    return box["seen"]


def test_two_sequential_runs_on_one_agent_get_different_envelopes(
    workspace: Path,
) -> None:
    """Invariant 2, and its precondition 1. One agent, two runs, two different
    sets of rights — measured at the gate, not by reading a config."""
    agent = _agent(workspace)

    with_tests = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "run_tests"),
        dry_run=True, include_tests=True)
    without_tests = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "run_tests"),
        dry_run=True, include_tests=False)

    assert with_tests != "deny", (
        "the run that asked for tests could not call run_tests, so the two runs "
        "do not actually differ and this proves nothing"
    )
    assert without_tests == "deny", (
        "the run that refused tests could still call run_tests — one agent is "
        "not carrying two different envelopes"
    )


def test_a_runs_narrowing_does_not_outlive_it(workspace: Path) -> None:
    """Invariant 3, the restriction direction. What one run forbids must be
    forgotten before the next begins."""
    agent = _agent(workspace)
    before = _verdict(agent, "rss_fetch")
    inside = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "rss_fetch"),
        dry_run=True, include_tests=True)

    assert before != "deny"
    assert inside == "deny", "the run did not narrow anything"
    assert _verdict(agent, "rss_fetch") == before, (
        "a run's restriction outlived the run — the next one inherits a limit "
        "nobody gave it"
    )


def test_a_runs_widening_does_not_outlive_it(workspace: Path) -> None:
    """Invariant 3, the permission direction — the half that would matter if a
    run were ever allowed to lift something. `unblock_tools` narrowly restores
    access to a tool the run itself would otherwise block."""
    agent = _agent(workspace)
    blocked = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "web_search"),
        dry_run=True, include_tests=True)
    unblocked = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "web_search"),
        dry_run=True, include_tests=True,
        unblock_tools=frozenset({"web_search"}))
    after = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "web_search"),
        dry_run=True, include_tests=True)

    assert blocked == "deny"
    assert unblocked != "deny", "the narrow unblock did not take effect"
    assert after == "deny", (
        "a permission granted to one run survived into the next — the unblock "
        "became a standing right"
    )


def test_the_monotone_field_is_the_shape_the_others_should_have(
    workspace: Path,
) -> None:
    """Invariant 6, positive half. `suppress_durable_learning_writes` is written
    as `previous or config.dry_run`, so a run can add suppression and never
    remove it. This is the invariant already satisfied, in the same block of
    code as the one that is not."""
    agent = _agent(workspace)
    agent.suppress_durable_learning_writes = True

    inside = _observe_during_run(
        agent, workspace,
        lambda: bool(getattr(agent, "suppress_durable_learning_writes", False)),
        dry_run=False, include_tests=True)

    assert inside is True, (
        "a run lifted the host's suppression of durable writes — the one field "
        "written to narrow only stopped doing so"
    )


def test_a_run_cannot_lift_the_hosts_dry_run(workspace: Path) -> None:
    """Invariant 6, the half that was red.

    Banked 2026-08-21 and closed the same day. `gateway_dry_run` used to be
    assigned `bool(config.dry_run)` outright, so a task configured live turned
    OFF a host that was in dry-run. The host field is no longer written at all:
    the run carries its own demand and `core/loop_step_execution.py` ORs the
    two at the gateway, so the host's dry-run cannot be lowered by anything a
    task asks for.
    """
    agent = _agent(workspace)
    agent.gateway_dry_run = True

    inside = _observe_during_run(
        agent, workspace,
        lambda: bool(getattr(agent, "gateway_dry_run", False)),
        dry_run=False, include_tests=True)

    assert inside is True, (
        "the host was in dry-run and a single task configuration turned it off "
        "for the duration of that task — the run raised a host restriction "
        "instead of only lowering its own"
    )


def test_a_nested_restriction_cannot_widen_the_one_around_it() -> None:
    """Invariant 6, the nesting half — found by breaking the mechanism.

    The host-versus-run case above was already pinned. Rewriting
    `run_restrictions` to REPLACE rather than union left every one of those
    green, because a single scope that is entered and exited behaves the same
    either way. The difference only shows when one restricted scope sits inside
    another, which is what a run started from inside a run actually is.
    """
    from core.run_context import (
        run_blocked_tools,
        run_demands_dry_run,
        run_restrictions,
    )

    with run_restrictions(blocked_tools={"outer_tool"}, dry_run=True):
        assert run_blocked_tools() == frozenset({"outer_tool"})
        assert run_demands_dry_run() is True

        with run_restrictions(blocked_tools={"inner_tool"}, dry_run=False):
            assert run_blocked_tools() == frozenset({"outer_tool", "inner_tool"}), (
                "the inner scope dropped a restriction the outer one imposed"
            )
            assert run_demands_dry_run() is True, (
                "a nested scope turned OFF the dry-run demanded around it"
            )

        assert run_blocked_tools() == frozenset({"outer_tool"}), (
            "leaving the inner scope did not restore the outer restriction"
        )

    assert run_blocked_tools() == frozenset()
    assert run_demands_dry_run() is False


def test_a_fresh_run_identity_does_not_shed_the_restrictions_around_it() -> None:
    """The same property for `run_scope`: minting a new run id inside a narrowed
    scope must not become a way to start clean. Every AgentLoop.run enters one,
    so this is the path a nested run actually takes."""
    from core.run_context import run_blocked_tools, run_restrictions, run_scope

    with run_restrictions(blocked_tools={"outer_tool"}, dry_run=True), run_scope("run_probe"):
        assert run_blocked_tools() == frozenset({"outer_tool"}), (
            "a new run identity started without the restriction it was born under"
        )

