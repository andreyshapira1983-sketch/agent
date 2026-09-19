"""Sequential reusability of one AgentLoop, measured — and NOT "WHO LIVES".

The claim in the first version of this file was too wide, and it broke the rule
this repository wrote down the same day: a trace id is what the agent DID at a
moment, never who lived. Corrected below, with the three registers kept apart.

The question was "what concretely prevents queue, learn, goal, self-build,
hygiene and campaign from being actions of the SAME continuing AgentLoop
instead of separate temporary instances". The measurement answers it in a way
neither side expected.

PROVEN: an existing `AgentLoop` can be reused for sequential work on the
measured stubbed / dry-run path, and reuse itself raises no immediate lifecycle
conflict. One instance ran an autonomous pass, a hygiene pass, the self-build
organ and two further passes with no failure. The self-build organ did not even
need persuading — it already takes `build_agent_fn`, so handing it the existing
agent is an injection the code offers.

OBSERVED: three chosen in-process indicators did not change across the measured
work — `memory is None`, the length of `compensation_log`, and
`last_replan_exhausted`.

UNPROVEN, and none of it should be read in: canonical subject continuity;
equivalence of reuse and rebuild; that no significant state transfers; the full
set of production organs (campaign is never called here); continuity across a
restart. The session trace id staying the same proves that one logger was
reused, not that one subject persisted — there is no agent identity in this
codebase for a test to observe.

What that adds up to is narrow and still worth having: **no prohibition on
reusing one AgentLoop was found.** That is a different sentence from "WHO LIVES
is answered", and the difference is the whole point of writing it down.

On the three indicators watched, reuse gained nothing — the unattended profile
sets `with_memory=False`, so there was no working memory to carry. Whether
anything ELSE would carry is not measured here, and the honest reading is that
the lifecycle question is not blocked by an obvious conflict, not that it is
solved.

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


def _session_trace(agent) -> str:
    """The LOGGER's trace id — deliberately not called identity.

    It names a session, not a subject; `docs/audit/AUTONOMY_FREEZE.md` refuses
    a persisted trace id as a continuity fix, so a test must not quietly treat
    an unchanged one as evidence of a continuing agent. Watched here only to
    show the instance was not silently swapped mid-sequence.
    """
    return str(getattr(agent.log, "trace_id", ""))


def test_one_instance_can_be_reused_for_sequential_work(workspace: Path) -> None:
    agent = _agent(workspace)
    trace = _session_trace(agent)

    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"
    agent.run_maintenance_pass(dry_run=True)
    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"
    assert AutonomousRuntime(agent, workspace=workspace).run(_config()).status == "completed"

    assert _session_trace(agent) == trace, (
        "the logger's trace id changed mid-sequence, so the instance was swapped "
        "and this measured something other than reuse. Not an identity claim: "
        "there is no agent identity here to keep."
    )


def test_three_watched_indicators_do_not_accumulate(workspace: Path) -> None:
    """Three indicators, named as three — not "the state".

    If this ever fails, something finally accumulates in one of them, and THEN
    rebuilding the instance starts to cost something. It says nothing about the
    fields it does not watch."""
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
