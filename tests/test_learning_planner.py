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


def test_apply_staleness_returns_score_when_stale_hours_zero(workspace: Path):
    """stale_hours <= 0 should disable the staleness adjustment entirely."""
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "README.md").write_text("ov", encoding="utf-8")

    recent_ts = datetime.now(timezone.utc).isoformat()
    fresh = MagicMock(); fresh.last_read_at = recent_ts
    registry = MagicMock(); registry.get_source = lambda sid: fresh

    plan = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=registry, stale_hours=0.0
    )
    # without staleness applied loop.py keeps full score; both selected
    assert "core/loop.py" in plan.source_paths


def test_apply_staleness_skips_when_record_missing(workspace: Path):
    """Registry returns None for a path → score unchanged."""
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")

    registry = MagicMock(); registry.get_source = lambda sid: None
    plan = LearningPlanner().plan(
        workspace=workspace, limit=1, source_registry=registry, stale_hours=6.0
    )
    assert "core/loop.py" in plan.source_paths


def test_apply_staleness_skips_when_last_read_empty(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")

    record = MagicMock(); record.last_read_at = ""
    registry = MagicMock(); registry.get_source = lambda sid: record
    plan = LearningPlanner().plan(
        workspace=workspace, limit=1, source_registry=registry, stale_hours=6.0
    )
    assert "core/loop.py" in plan.source_paths


def test_apply_staleness_skips_when_timestamp_unparseable(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")

    record = MagicMock(); record.last_read_at = "not-a-real-iso-timestamp"
    registry = MagicMock(); registry.get_source = lambda sid: record
    plan = LearningPlanner().plan(
        workspace=workspace, limit=1, source_registry=registry, stale_hours=6.0
    )
    assert "core/loop.py" in plan.source_paths


def test_apply_staleness_keeps_score_when_record_older_than_window(workspace: Path):
    """Files read longer ago than stale_hours are not deprioritised."""
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "README.md").write_text("ov", encoding="utf-8")

    from datetime import timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    record = MagicMock(); record.last_read_at = old_ts
    registry = MagicMock(); registry.get_source = lambda sid: record

    plan = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=registry, stale_hours=6.0
    )
    # README still wins, but loop.py keeps original score (no -60 penalty applied)
    assert "core/loop.py" in plan.source_paths


def test_goal_terms_memory_keyword_picks_memory_files(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "core" / "memory_policy.py").write_text("m", encoding="utf-8")
    (workspace / "core" / "ingestion.py").write_text("i", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="памят", limit=2)
    assert "core/memory_policy.py" in plan.source_paths
    assert "core/ingestion.py" in plan.source_paths


def test_goal_terms_role_keyword_picks_router(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "core" / "role_router.py").write_text("r", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="role router", limit=1)
    assert "core/role_router.py" in plan.source_paths


def test_goal_terms_tool_keyword_picks_tools(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "tools" / "shell_exec.py").write_text("s", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="инструмент", limit=1)
    assert "tools/shell_exec.py" in plan.source_paths


def test_goal_terms_verifier_keyword_picks_verifier(workspace: Path):
    (workspace / "core").mkdir()
    (workspace / "core" / "verifier.py").write_text("v", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="вериф evidence", limit=1)
    assert "core/verifier.py" in plan.source_paths


def test_runtime_directory_files_get_scored(workspace: Path):
    (workspace / "runtime").mkdir()
    (workspace / "runtime" / "agent.py").write_text("rt", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, limit=1)
    assert "runtime/agent.py" in plan.source_paths


def test_unsupported_extension_skipped(workspace: Path):
    (workspace / "binary.bin").write_text("x", encoding="utf-8")
    (workspace / "README.md").write_text("ov", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, limit=2)
    assert "README.md" in plan.source_paths
    assert "binary.bin" not in plan.source_paths
    assert any("binary.bin" in s for s in plan.skipped_paths)
