"""Proof 7 of 8: two overlapping runs must not leak authority into each other.

Banked RED on 2026-08-21 and closed the same day; the demonstration is kept
because it is what the fix has to keep satisfying. The mechanism is already in the tree and already named in MIR-114:
`AutonomousRuntime._task_goal` narrows the agent for the duration of one run by
MUTATING shared objects and putting them back in a `finally` —

    previous_blocked = policy.blocked_tools
    policy.blocked_tools = policy.blocked_tools | to_block
    try:    self.agent.run(...)
    finally: policy.blocked_tools = previous_blocked

That is correct while exactly one run holds the object, which is why every
sequential test of it passes. It stops being correct the moment two runs
overlap on one host — the thing a consolidation creates by definition.

The order is imposed with events rather than hoped for, so this is a
demonstration and not a race:

    A enters, snapshots the empty block set, installs its own
    B enters, snapshots A's set, installs its own
    A returns  -> its `finally` restores the set A found: EMPTY
    B is still running, and now holds no blocks at all

B's own configuration forbids `spawn_subagent`. After A's `finally`, B may call
it. Nothing raises, nothing is logged, and B's own guard is simply gone.

The sequential control next to it must stay GREEN. Without it this file would
only be saying "mutating shared state is bad", which is an opinion; with it the
file says the leak appears exactly when the runs overlap, which is a fact about
this code.

No implementation is proposed here. What must be true is stated; how a run
comes to carry its own envelope is the decision this proof exists to protect.
"""
from __future__ import annotations

import threading
from pathlib import Path

from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.models import Action
from tests.test_dry_run_learning_state import _durable_agent
from tools.base import Tool

#: A name from the real unattended block set (core/autonomous_runtime.py:74)
#: that is NOT in _UNBLOCKABLE_TOOLS, so no configuration can lift it.
_BLOCKED_EXAMPLE = "rss_fetch"


class _StubBlockedTool(Tool):
    """A registered, read-only, do-nothing tool carrying a blocked name.

    It exists so the gate's answer has ONE possible cause. `policy.check` says
    deny both when a run-scoped block is in force and when the tool is simply
    absent from the registry, and the first version of this file read the second
    as the first. With the tool registered and read-only, a deny can only be the
    block, and an allow can only be its removal.
    """

    name = _BLOCKED_EXAMPLE
    description = "probe stub: registered so the gate has one reason, never run"
    risk = "read_only"

    def run(self, **kwargs):  # pragma: no cover - never invoked
        raise AssertionError("the probe stub must never be executed")


def _verdict(agent) -> str:
    """What the real refusal site says about the blocked name, right now."""
    return agent.policy.check(
        Action(step_id="probe", type="tool_call", tool_name=_BLOCKED_EXAMPLE,
               parameters={})
    ).decision
_TIMEOUT = 20.0


class _Rendezvous:
    """Two runs, driven into a fixed interleaving without sleeping."""

    def __init__(self) -> None:
        self.inside = {"a": threading.Event(), "b": threading.Event()}
        self.release = {"a": threading.Event(), "b": threading.Event()}
        self.seen: dict[str, object] = {}
        self.errors: list[BaseException] = []

    def wait_inside(self, who: str) -> None:
        assert self.inside[who].wait(_TIMEOUT), f"run {who} never reached its run()"

    def let_finish(self, who: str) -> None:
        self.release[who].set()


def _blocks(agent) -> frozenset[str]:
    """The set the GATE would use, host ceiling plus whatever this run adds.

    Reading `policy.blocked_tools` alone stopped being the right sensor when a
    run's narrowing moved off the shared field: that attribute is now the host's
    ceiling only, and a run's own blocks live in its context. Kept for the
    failure messages; the assertions below go through `_verdict`, which is the
    site that actually refuses.
    """
    return agent.policy._effective_blocked_tools()


def _agent_with_the_blocked_tool(workspace: Path):
    """The fixture agent, plus the stub — so authority is observable, not just
    state. Registered before any run, so `_verdict` starts at allow."""
    agent = _durable_agent(workspace)
    agent.registry.register(_StubBlockedTool())
    return agent


# NOTE on the sensor, because the first version of this file got it wrong.
# The obvious probe is `policy.check(Action(tool_name="spawn_subagent"))`, and it
# answers "deny" whether the run-scoped block is in place OR the tool is simply
# not in this agent's registry. Same answer, different cause — the test passed
# while the leak was happening. The block SET is the honest sensor here: it is
# the thing the run installs and the thing another run's cleanup removes.


def _run_one(runtime, agent, rv: _Rendezvous, who: str, config) -> None:
    def fake_run(*_args, **_kwargs):
        rv.seen[f"{who}_entry_blocks"] = _blocks(agent)
        rv.seen[f"{who}_entry_verdict"] = _verdict(agent)
        rv.inside[who].set()
        assert rv.release[who].wait(_TIMEOUT), f"run {who} was never released"
        rv.seen[f"{who}_exit_blocks"] = _blocks(agent)
        rv.seen[f"{who}_exit_verdict"] = _verdict(agent)
        raise _Done()

    agent.run = fake_run  # type: ignore[method-assign]
    try:
        runtime._task_goal(AutonomousTask("goal", f"probe {who}"), config)
    except _Done:
        pass
    except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
        rv.errors.append(exc)


class _Done(Exception):
    """Ends the fake run once its observations are taken."""


def _config(**kw) -> AutonomousRuntimeConfig:
    return AutonomousRuntimeConfig(goal="probe", dry_run=True, limit=1, **kw)


def test_a_single_run_is_narrowed_and_restored(workspace: Path) -> None:
    """Control, and it must stay green: one run at a time works exactly as
    written. If this ever fails, the proof below is measuring something else."""
    agent = _agent_with_the_blocked_tool(workspace)
    runtime = AutonomousRuntime(agent, workspace=workspace)
    before = _blocks(agent)
    rv = _Rendezvous()
    rv.release["a"].set()
    _run_one(runtime, agent, rv, "a", _config())

    assert _BLOCKED_EXAMPLE in rv.seen["a_entry_blocks"]
    assert rv.seen["a_entry_verdict"] == "deny", (
        "the run did not actually narrow authority, so nothing below means anything"
    )
    assert rv.seen["a_exit_verdict"] == "deny", (
        "a run on its own lost its restriction before it finished"
    )
    assert _blocks(agent) == before, "the run's narrowing outlived the run"
    assert _verdict(agent) != "deny", (
        "after the run, the restriction outlived it — the control is not clean"
    )


def test_an_overlapping_run_keeps_its_own_blocks(workspace: Path) -> None:
    agent = _agent_with_the_blocked_tool(workspace)
    rv = _Rendezvous()
    runtime_a = AutonomousRuntime(agent, workspace=workspace)
    runtime_b = AutonomousRuntime(agent, workspace=workspace)

    thread_a = threading.Thread(
        target=_run_one, args=(runtime_a, agent, rv, "a", _config()), daemon=True)
    thread_b = threading.Thread(
        target=_run_one, args=(runtime_b, agent, rv, "b", _config()), daemon=True)

    thread_a.start()
    rv.wait_inside("a")          # A holds its own block set
    thread_b.start()
    rv.wait_inside("b")          # B snapshots A's set and installs its own
    rv.let_finish("a")           # A returns; its finally restores what A found
    thread_a.join(_TIMEOUT)
    rv.let_finish("b")           # B looks again, still inside its own run
    thread_b.join(_TIMEOUT)

    assert not rv.errors, f"a run failed for an unrelated reason: {rv.errors}"
    assert rv.seen["b_entry_verdict"] == "deny", (
        "the second run never held the restriction, so this proves nothing"
    )
    assert rv.seen["b_exit_verdict"] == "deny", (
        "while still running, the second run's authority WIDENED: the real gate "
        f"answered {rv.seen['b_entry_verdict']!r} on entry and "
        f"{rv.seen['b_exit_verdict']!r} after another run's cleanup, with the "
        f"block set going from {sorted(rv.seen['b_entry_blocks'])} to "
        f"{sorted(rv.seen['b_exit_blocks'])}. The tool is registered and "
        "read-only, so deny can only be the block and allow can only be its "
        "removal. Nothing raised, nothing logged"
    )
