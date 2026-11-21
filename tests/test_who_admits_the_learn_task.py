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

import ast
import pathlib

from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
)

_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "core" / "autonomous_runtime.py"


def _recipe_ast() -> ast.FunctionDef:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "_build_queue":
                    return fn
    raise AssertionError(
        "_build_queue is gone or renamed — the site moved, re-measure before "
        "trusting anything this file says"
    )


def test_the_recipe_cannot_see_the_world() -> None:
    """Premise: no observed state reaches the decision, by construction."""
    fn = _recipe_ast()
    self_uses = sorted(
        ast.unparse(n) for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and ast.unparse(n).startswith("self")
    )
    assert self_uses == [], (
        f"_build_queue now reads {self_uses} — it can see something about the "
        "agent, so this measurement's premise no longer holds"
    )
    # A set: `config.goal` is read twice, and the count is not the point —
    # WHICH fields can influence the decision is.
    reads = {
        ast.unparse(n) for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and ast.unparse(n).startswith("config")
    }
    assert reads == {"config.goal", "config.include_goal",
                     "config.include_proposals", "config.include_tests"}, (
        f"the recipe now consults {sorted(reads)}; a new input may be a way for "
        "something observed to reach the decision — check before assuming not"
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
