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
    for name in ("spawn_subagent", "run_tests", "semantic_scholar_search"):
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


def test_a_runs_widening_does_not_outlive_it(workspace: Path) -> None:
    """Invariant 3, the permission direction — the half that would matter if a
    run were ever allowed to lift something. `unblock_tools` narrowly restores
    access to a tool the run itself would otherwise block.

    Проверяется на поиске научных работ: 2026-09-01 чтение веба открыто всегда
    (слово оператора), поэтому инвариант «разблокировка не переживает прогон»
    держится теперь на инструменте, который ДЕЙСТВИТЕЛЬНО закрыт. Сам инвариант
    не изменился: временное право обязано умирать вместе со своим прогоном.
    """
    agent = _agent(workspace)
    blocked = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "semantic_scholar_search"),
        dry_run=True, include_tests=True)
    unblocked = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "semantic_scholar_search"),
        dry_run=True, include_tests=True,
        unblock_tools=frozenset({"semantic_scholar_search"}))
    after = _observe_during_run(
        agent, workspace, lambda: _verdict(agent, "semantic_scholar_search"),
        dry_run=True, include_tests=True)

    assert blocked == "deny"
    assert unblocked != "deny", "the narrow unblock did not take effect"
    assert after == "deny", (
        "a permission granted to one run survived into the next — the unblock "
        "became a standing right"
    )
