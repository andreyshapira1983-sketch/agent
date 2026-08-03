"""MIR-068 — a queued daemon goal executes (operator's autonomy grant, 2026-08-03).

Measured live before the fix: two `auto_run` tasks queued with real goal
texts ran to `task_done / completed` while only `status`+`learn` executed —
the goal text was carried and silently dropped, because `_config_from_task`
never passed `include_goal` (default False) even though the daemon goal path
has its own posture blocks and the queue schema has a `goal` field.

The operator ruled: execute. The wire is ONE field — `include_goal` — and
every existing gate stays exactly as it was: `_build_queue` still refuses a
goal task for the default "project health" goal, `_task_goal` still applies
the unattended posture blocks (no subagents, no network), the gateway still
enforces dry-run, and the agent-runs budget still bounds the spend.
"""
from __future__ import annotations

from core.autonomous_runtime import (
    _AUTONOMOUS_GOAL_BLOCKED_TOOLS,
    AutonomousRuntime,
    _config_from_task,
)
from core.task_queue import RuntimeTask


def _task(goal: str) -> RuntimeTask:
    return RuntimeTask(kind="auto_run", goal=goal, include_tests=False)


def test_config_from_task_carries_include_goal():
    config = _config_from_task(_task("Проверь файл журнала и ответь числом."))
    assert config.include_goal is True
    assert config.goal == "Проверь файл журнала и ответь числом."


def test_default_goal_still_runs_no_goal_task():
    """The `_build_queue` guard is the second lock: even with include_goal
    wired, the placeholder goal never becomes a goal task."""
    config = _config_from_task(_task("project health"))
    queue = AutonomousRuntime.__new__(AutonomousRuntime)._build_queue(config)
    assert [t.kind for t in queue] == ["status", "learn"]


def test_real_goal_becomes_a_goal_task_after_status_and_learn():
    config = _config_from_task(_task("Сколько строк в файле журнала?"))
    queue = AutonomousRuntime.__new__(AutonomousRuntime)._build_queue(config)
    kinds = [t.kind for t in queue]
    assert kinds == ["status", "learn", "goal"]
    assert queue[-1].description == "Сколько строк в файле журнала?"


def test_the_posture_blocks_still_guard_the_unattended_goal_path():
    """The autonomy grant does not widen tooling: the daemon goal path keeps
    refusing subagents and network egress by its own constant."""
    assert "spawn_subagent" in _AUTONOMOUS_GOAL_BLOCKED_TOOLS
    for tool in ("web_search", "web_fetch", "rss_fetch", "semantic_scholar_search"):
        assert tool in _AUTONOMOUS_GOAL_BLOCKED_TOOLS
