"""Banked: the same leak, on the third axis — what the planner is told exists.

`policy.blocked_tools` and `gateway_dry_run` moved onto the run on 2026-08-21.
`planner.hidden_tools` did not, and it still carries the shape those two lost:

    previous_hidden = planner.hidden_tools
    planner.hidden_tools = to_block
    try:    agent.run(...)
    finally: planner.hidden_tools = previous_hidden

Premise measured before the experiment, not read: hiding a tool changes the
LIVE advertised surface. `_build_user_prompt` (called from `Planner.plan`)
lists `registry.list()` minus `hidden_tools` and, when anything is hidden, adds
an explicit `[UNAVAILABLE_TOOLS=…]` directive whose stated purpose in the code
is to stop the planner selecting a run-blocked tool and generating noisy
policy_blocked replans. Measured: 15 tools advertised, 14 after hiding one, and
the directive appears.

Note what that stated purpose is and is not: it is the reason the directive was
written, quoted from the code. It is not evidence that removing the directive
causes anything.

The overlap, driven by events into a fixed order:

    A enters, hides the unattended block set
    B enters, snapshots what A hid, hides its own
    A returns -> its finally restores what A FOUND: nothing hidden
    B is still running, and is told the full surface again

WHAT IS PROVEN is exactly this: the advertised surface and the directive change
under a neighbouring run's cleanup, mid-run. Nothing more.

WHAT IS NOT PROVEN, and must not be read into the result:

  - that the tool becomes callable. It does not — the gate still answers deny,
    because `PolicyGate` now computes host | run and no longer lives on a shared
    field. Measured in the same experiment.
  - **that any planning is wasted, or that a replan happens at all. UNPROVEN.**
    This test never calls `Planner.plan`: `agent.run` is replaced by a stub that
    records the surface and raises, so no plan is produced and no tool is
    selected. That a wider surface would lead the planner to choose a forbidden
    tool is a plausible inference from the directive's stated purpose, and an
    inference is not a measurement. Showing it would need a run that actually
    plans, and this file does not do that.

Worth keeping in view, also bounded: before the policy axis was moved, this leak
coincided with that one, so such a proposal would have PASSED THE GATE. Whether
it would then have executed depends on the gateway, the approval path and the
tool itself, none of which this experiment touches. The gate holding today is
defence in depth doing its job, not a reason to leave this.

FIXED 2026-09-25: the run's block set is read from the run context by
`Planner.effective_hidden_tools`, the same form the policy axis took on
2026-08-21; `hidden_tools` keeps only the host's own set.
"""
from __future__ import annotations

import threading
from pathlib import Path

from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import build_agent
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)

_HIDDEN_EXAMPLE = "spawn_subagent"
_TIMEOUT = 20.0


class _Done(Exception):
    """Ends the fake run once its observation is taken."""


def _agent(workspace: Path):
    return build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)


def _advertised(agent) -> tuple[frozenset[str], bool]:
    """What the planner is actually told, read from the prompt it would send.

    Not `planner.hidden_tools`: that is the field: this is the surface. A test
    on the field would pass while the surface leaked.
    """
    prompt = agent.planner._build_user_prompt("probe", None)
    marker = "registered tools: "
    line = next((row for row in prompt.splitlines() if row.startswith(marker)), "")
    names = frozenset(
        part.strip() for part in line[len(marker):].split(",") if part.strip()
    )
    return names, "[UNAVAILABLE_TOOLS=" in prompt


def _run_one(runtime, agent, box: dict, who: str, inside, release) -> None:
    def fake_run(*_a, **_k):
        box[f"{who}_entry"] = _advertised(agent)
        inside[who].set()
        assert release[who].wait(_TIMEOUT), f"run {who} was never released"
        box[f"{who}_exit"] = _advertised(agent)
        raise _Done()

    agent.run = fake_run  # type: ignore[method-assign]
    try:
        runtime._task_goal(
            AutonomousTask("goal", f"probe {who}"),
            AutonomousRuntimeConfig(goal="probe", dry_run=True, limit=1),
        )
    except _Done:
        pass


def test_hiding_a_tool_changes_what_the_planner_is_told(workspace: Path) -> None:
    """The premise, measured. Without this the experiment below means nothing."""
    agent = _agent(workspace)
    before, before_directive = _advertised(agent)
    assert _HIDDEN_EXAMPLE in before
    assert before_directive is False

    agent.planner.hidden_tools = frozenset({_HIDDEN_EXAMPLE})
    after, after_directive = _advertised(agent)
    assert _HIDDEN_EXAMPLE not in after
    assert after_directive is True
    assert after < before


def test_a_single_run_hides_and_then_restores_the_surface(workspace: Path) -> None:
    """Control, and it must stay green: alone, the mechanism behaves. If this
    fails, the banked case below is measuring something else."""
    agent = _agent(workspace)
    outside, _ = _advertised(agent)
    box: dict = {}
    inside = {"a": threading.Event()}
    release = {"a": threading.Event()}
    release["a"].set()
    _run_one(AutonomousRuntime(agent, workspace=workspace), agent, box, "a",
             inside, release)

    entry_names, entry_directive = box["a_entry"]
    assert _HIDDEN_EXAMPLE not in entry_names, "the run advertised a blocked tool"
    assert entry_directive is True
    assert _advertised(agent)[0] == outside, "the run's hiding outlived the run"


def test_an_overlapping_run_keeps_its_own_tool_surface(workspace: Path) -> None:
    agent = _agent(workspace)
    box: dict = {}
    inside = {k: threading.Event() for k in ("a", "b")}
    release = {k: threading.Event() for k in ("a", "b")}

    thread_a = threading.Thread(
        target=_run_one,
        args=(AutonomousRuntime(agent, workspace=workspace), agent, box, "a",
              inside, release),
        daemon=True)
    thread_b = threading.Thread(
        target=_run_one,
        args=(AutonomousRuntime(agent, workspace=workspace), agent, box, "b",
              inside, release),
        daemon=True)

    thread_a.start()
    assert inside["a"].wait(_TIMEOUT)
    thread_b.start()
    assert inside["b"].wait(_TIMEOUT)
    release["a"].set()
    thread_a.join(_TIMEOUT)
    release["b"].set()
    thread_b.join(_TIMEOUT)

    entry_names, _ = box["b_entry"]
    exit_names, exit_directive = box["b_exit"]
    assert _HIDDEN_EXAMPLE not in entry_names, (
        "the second run never had the tool hidden, so this proves nothing"
    )
    assert _HIDDEN_EXAMPLE not in exit_names, (
        "while still running, the second run was advertised "
        f"{_HIDDEN_EXAMPLE!r} again — another run's cleanup restored the surface "
        f"it found, going from {len(entry_names)} tools to {len(exit_names)}"
    )
    assert exit_directive is True, (
        "the UNAVAILABLE_TOOLS directive vanished mid-run; the planner is now "
        "free to select a tool the gate will refuse"
    )
