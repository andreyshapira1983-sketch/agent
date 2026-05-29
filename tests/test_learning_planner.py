from pathlib import Path

import pytest

from core.learning_planner import LearningPlanner


def test_learning_planner_prefers_architecture_readme_and_core(workspace: Path):
    (workspace / "README.md").write_text("overview", encoding="utf-8")
    (workspace / "архитектура автономного Агента.txt").write_text("architecture", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "notes.tmp").write_text("ignore", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, limit=3)

    assert "README.md" in plan.source_paths
    assert "архитектура автономного Агента.txt" in plan.source_paths
    assert "core/loop.py" in plan.source_paths


def test_learning_planner_focuses_goal_specific_sources(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "core" / "self_repair.py").write_text("repair", encoding="utf-8")
    (workspace / "core" / "memory_policy.py").write_text("memory", encoding="utf-8")
    (workspace / "tests" / "test_self_repair_e2e.py").write_text("repair tests", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="self-repair", limit=2)

    assert "core/self_repair.py" in plan.source_paths
    assert "tests/test_self_repair_e2e.py" in plan.source_paths


def test_learning_planner_rejects_workspace_escape(workspace: Path, tmp_path: Path):
    outside = workspace.parent / "outside-learning-root"
    outside.mkdir()
    with pytest.raises(PermissionError):
        LearningPlanner().plan(workspace=workspace, root=str(outside))
