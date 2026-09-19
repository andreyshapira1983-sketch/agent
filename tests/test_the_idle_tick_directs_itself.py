"""An empty queue is not the same as nothing to do.

Background: docs/CODE_NOTES.md, "Idle self-direction".
"""
from __future__ import annotations

import json
from pathlib import Path

import agent_tick
from core.self_build_memory import idle_self_direction


def _tick_events(workspace: Path) -> list[dict]:
    log_path = workspace / "logs" / "daemon_tick.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_an_idle_tick_says_what_it_would_do(workspace: Path, monkeypatch):
    """The queue is empty and the tick still names its own next action."""
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("an idle tick must not build an agent")

    from app import bootstrap
    monkeypatch.setattr(bootstrap, "build_agent", _boom)

    agent_tick.run_tick(workspace, dry_run=True)

    events = _tick_events(workspace)
    names = [e.get("event") for e in events]
    assert "no_pending_tasks" in names, "precondition: the queue must be empty"
    assert "self_direction" in names, (
        "an idle tick reported nothing to do and proposed nothing — the agent "
        "can only notice its own defects when a human runs a command"
    )
    assert "self_direction_error" not in names, [
        e for e in events if e.get("event") == "self_direction_error"
    ]


def test_it_costs_no_model_call(workspace: Path, monkeypatch):
    """A tick firing every 30 minutes may not pay a model to learn it is idle."""
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")
    result = idle_self_direction(workspace, heartbeat=None)

    assert result.get("action"), (
        "the priority brain returned no action at all; it is contracted to "
        "always return one, `observe` when nothing is pressing"
    )
    assert isinstance(result["open_issues"], int)


def test_the_agents_own_failure_becomes_a_durable_issue(workspace: Path):
    """Journal -> registry with nobody asking. A failure is seeded: an empty
    registry in a fresh workspace is honest and would prove nothing."""
    from core.self_improvement_issues import SelfImprovementIssueRegistry
    from core.smart_memory import EpisodeRecord, EpisodicMemoryStore

    store = EpisodicMemoryStore(workspace / "data" / "episodic_memory.jsonl")
    store.save(EpisodeRecord(
        goal="repair the splitter",
        question="self-split of core/loop.py",
        outcome="failed",
        summary="self-apply rolled_back: duplicate base class after the split",
    ))

    before = idle_self_direction(workspace, heartbeat=None)

    issues = SelfImprovementIssueRegistry(
        workspace / "data" / "self_improvement_issues.jsonl"
    ).unresolved()
    assert issues, (
        "the agent logged its own failed repair and nothing turned it into an "
        "issue it can see later — the loop stays open until a human runs a "
        "command"
    )
    assert before["open_issues"] >= 1
    assert before["action"], "an open issue and still no proposed next action"
