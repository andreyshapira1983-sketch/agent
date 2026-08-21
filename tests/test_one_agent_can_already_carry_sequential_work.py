"""WHO LIVES, measured: nothing stops one agent from carrying a sequence.

The question was "what concretely prevents queue, learn, goal, self-build,
hygiene and campaign from being actions of the SAME continuing AgentLoop
instead of separate temporary instances". The measurement answers it in a way
neither side expected.

Nothing prevents it. One instance runs an autonomous pass, a hygiene pass, the
self-build organ and two more passes without a single failure, keeping its
identity throughout. The self-build organ did not even need persuading — it
already takes `build_agent_fn`, so handing it the existing agent is an
injection the code offers.

The obstacle is the opposite of what a lifecycle problem looks like: reuse does
not break, it simply GAINS NOTHING. Every in-process field is identical before
and after every piece of work — `memory` is None because the unattended profile
sets `with_memory=False`, the compensation log stays empty, the replan flag
stays False. Reusing the instance and rebuilding it are observationally the
same, because the instance was given nothing to carry.

So "one continuing agent" is not blocked by the runtime. It is empty of
consequence until memory belongs to the subject — which is why the two were
called one task, with the halves the other way round from the assumption: the
lifecycle half is already possible, the memory half is what is missing.

What this does NOT measure, stated so the green does not read as more:
`agent.run` is stubbed, so nothing the synthesis path would accumulate is
exercised; dry-run means self-build skips its real work and durable writes are
suppressed; the workspace is a fresh temp directory; and four pieces of work
are not a long sequence.
"""
from __future__ import annotations

from pathlib import Path

from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import build_agent
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig


def _agent(workspace: Path):
    agent = build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)
    agent.run = lambda **_kwargs: "stubbed analysis"  # type: ignore[method-assign]
    return agent


def _config() -> AutonomousRuntimeConfig:
    return AutonomousRuntimeConfig(
        goal="probe", dry_run=True, limit=1, include_tests=False
    )


def _identity(agent) -> str:
    return str(getattr(agent.log, "trace_id", ""))


def test_one_instance_carries_a_sequence_of_organ_work(workspace: Path) -> None:
    agent = _agent(workspace)
    identity = _identity(agent)

    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"
    agent.run_maintenance_pass(dry_run=True)
    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"
    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"

    assert _identity(agent) == identity, (
        "the agent's identity changed while it was working — reuse is not reuse"
    )


def test_reuse_gains_nothing_because_the_instance_holds_nothing(
    workspace: Path,
) -> None:
    """The half that matters. If this ever fails, something finally accumulates
    in the instance, and THEN rebuilding it starts to cost something."""
    agent = _agent(workspace)

    def snapshot() -> tuple:
        return (
            agent.memory is None,
            len(getattr(agent, "compensation_log", []) or []),
            bool(getattr(agent, "last_replan_exhausted", False)),
        )

    before = snapshot()
    AutonomousRuntime(agent, workspace=workspace).run(_config())
    agent.run_maintenance_pass(dry_run=True)
    AutonomousRuntime(agent, workspace=workspace).run(_config())

    assert snapshot() == before, (
        f"in-process state changed across the sequence: {before} -> {snapshot()}. "
        "That is not a failure — it means continuity has started to have "
        "content, and this characterisation needs rewriting deliberately."
    )
    assert agent.memory is None, (
        "the unattended profile granted working memory; the premise of this "
        "measurement no longer holds"
    )
