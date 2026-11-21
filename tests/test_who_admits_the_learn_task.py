"""WHO DECIDES, upstream of ranking: who admits a task to the queue at all.

The previous measurement asked who ranks two admissible actions. This one is
higher in the stream: before asking "who decided which file is worth studying",
ask who decided that studying is what happens now.

`AutonomousRuntime._build_queue` (core/autonomous_runtime.py:1401) opens every
run with two tasks and then adds three more by config flag. Two facts, measured
rather than read.

PREMISE. The method makes ZERO references to `self` — checked by parsing it,
not by eye. No agent state, no workspace, no memory, no observation can reach
it. It is a pure function of `AutonomousRuntimeConfig`, reading exactly four
fields: goal, include_goal, include_proposals, include_tests. And the config
has no switch for learning at all: `learning_limit` and
`learning_writes_memory` govern HOW it learns, never WHETHER. The only way
`learn` does not run is `[: config.limit]` truncating it off the end — position,
not judgement.

CONSEQUENCE. The list is not decorative. As written, `status` and `learn`
execute; replace only the recipe and `learn` never runs. So this really is the
thing that disposes of work.

WHAT THIS CLAIMS, and it is deliberately one task: at this boundary
developer-written code holds admission and ordering authority over at least the
`learn` task, and the boundary offers the agent no way to decide whether that
work is justified now.

WHAT IT DOES NOT CLAIM. Nothing about `status` being wrong. A cheap look at
one's own state before acting may well be a platform procedure rather than an
executive decision, and this file takes no position on it. `learn` is the
flagged one because ingesting sources and planning study is work, chosen before
any deliberation happens.
"""
from __future__ import annotations

from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
)


def test_learn_is_admitted_whatever_the_config_says(workspace) -> None:
    """It survives every flag; only truncation removes it, and truncation is a
    length, not a judgement."""
    runtime = AutonomousRuntime.__new__(AutonomousRuntime)
    for kwargs in ({}, {"include_tests": False}, {"include_goal": True},
                   {"include_proposals": True}):
        config = AutonomousRuntimeConfig(goal="probe", dry_run=True, **kwargs)
        kinds = [t.kind for t in runtime._build_queue(config)]
        assert kinds[:2] == ["status", "learn"], (
            f"config {kwargs} produced {kinds} — the opening pair moved"
        )

    narrow = AutonomousRuntimeConfig(goal="probe", dry_run=True, limit=1)
    kept = [t.kind for t in runtime._build_queue(narrow)[: narrow.limit]]
    assert kept == ["status"], (
        "the only exclusion of learn is falling off the end of a slice"
    )
