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

import pytest

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


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-21 and banked (MIR-114): a run's envelope "
        "may only narrow. `suppress_durable_learning_writes` obeys that — it is "
        "assigned `previous or config.dry_run`. `gateway_dry_run` two lines "
        "above is assigned `bool(config.dry_run)` outright, so a run started "
        "with dry_run=False turns OFF a host that was in dry-run. Same block, "
        "two shapes. Fix unprescribed: make the assignment monotone, or give a "
        "run an envelope it cannot raise — different designs."
    ),
    strict=True,
)
def test_a_run_cannot_lift_the_hosts_dry_run(workspace: Path) -> None:
    """Invariant 6, negative half."""
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
