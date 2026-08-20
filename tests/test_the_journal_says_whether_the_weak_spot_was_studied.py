"""The line the operator reads must not call a substitution a success.

`reflection_learning_ingest sources=10 claims=80` is the only trace a run
leaves of its self-directed study, and it reports volume. Volume is silent
about whether the ten files had anything to do with the weak spot the agent
diagnosed — which, measured live (MIR-106), they often did not.

This is the live half of the prerequisite: `learning_grounding()` is unit
tested next door, but a classification that never reaches the journal changes
nothing an operator can see. Here the runtime is actually run.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from tests.test_dry_run_learning_state import _durable_agent

_TOPIC_LESSON = json.dumps(
    [
        {
            "insight": "retrieval keeps missing what was written",
            "action": "learn_more",
            "focus_area": "memory subsystem",
            "confidence": 0.8,
        }
    ]
)


def _seed_study_material(workspace: Path) -> None:
    """The planner skips a workspace with nothing worth reading, and then the
    ingest edge never runs at all. Two modules inside the temp polygon, so no
    organ is ever aimed at the live tree."""
    package = workspace / "core"
    package.mkdir(parents=True, exist_ok=True)
    module = '''"""A module with enough substance to be worth studying."""


def score(items):
    return sorted(items, key=len)[:3]


class Store:
    def __init__(self, path):
        self.path = path

    def load(self):
        return self.path.read_text(encoding="utf-8").splitlines()
'''
    for name in ("retrieval.py", "indexing.py"):
        (package / name).write_text(module, encoding="utf-8")


def _ingest_events(workspace: Path) -> list[dict]:
    events: list[dict] = []
    for path in (workspace / "logs").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"reflection_learning_ingest"' not in line:
                continue
            record = json.loads(line)
            if record.get("event") == "reflection_learning_ingest":
                events.append(record.get("payload", {}))
    return events


def test_an_unresolvable_weak_spot_is_named_in_the_ingest_record(
    workspace: Path,
) -> None:
    agent = _durable_agent(workspace)
    _seed_study_material(workspace)
    agent.llm.responses = [_TOPIC_LESSON]
    runtime = AutonomousRuntime(agent, workspace=workspace)
    runtime._run_reflection(AutonomousRuntimeConfig(goal="x", dry_run=True))

    events = _ingest_events(workspace)
    assert events, "reflection studied nothing — the fixture stopped exercising the edge"
    payload = events[-1]
    assert payload["grounding"] == "unresolvable", (
        f"the run studied {payload['sources']} files it did not diagnose and "
        f"recorded that as {payload.get('grounding')!r}"
    )
    assert payload["unmet_targets"] == ["memory subsystem"]
