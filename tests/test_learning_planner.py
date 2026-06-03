from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

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


def test_staleness_deprioritises_recently_ingested(workspace: Path):
    """Files ingested within stale_hours should score lower than fresh ones."""
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "README.md").write_text("overview", encoding="utf-8")

    # Mock a registry that says core/loop.py was read 1 hour ago (within 6h window)
    recent_ts = datetime.now(timezone.utc).isoformat()
    stale_record = MagicMock()
    stale_record.last_read_at = recent_ts

    registry = MagicMock()
    registry.get_source = lambda sid: stale_record if sid == "file:core/loop.py" else None

    plan_with = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=registry, stale_hours=6.0
    )
    plan_without = LearningPlanner().plan(workspace=workspace, limit=2)

    # README.md should be selected in both; core/loop.py deprioritised but not excluded
    assert "README.md" in plan_with.source_paths
    # With registry, README.md should appear before core/loop.py (higher effective score)
    paths = list(plan_with.source_paths)
    assert paths.index("README.md") < paths.index("core/loop.py")

    # Without registry nothing changes — loop.py still selected
    assert "core/loop.py" in plan_without.source_paths


def test_staleness_no_effect_when_registry_is_none(workspace: Path):
    """Passing source_registry=None leaves scores unchanged."""
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, limit=1, source_registry=None)
    assert "core/loop.py" in plan.source_paths
