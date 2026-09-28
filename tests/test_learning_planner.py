from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.doc_routing import is_confidence_evidence_diagnostic_question
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


def test_learning_planner_corporate_goal_prefers_doctrine_docs(workspace: Path):
    (workspace / "README.md").write_text("overview", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "core" / "architecture_audit.py").write_text("audit", encoding="utf-8")
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "knowledge" / "doctrine" / "future").mkdir(parents=True)
    (workspace / "knowledge" / "maps").mkdir(parents=True)
    (workspace / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md").write_text("corp", encoding="utf-8")
    (workspace / "knowledge" / "doctrine" / "CENTRAL_AGENT_GOVERNANCE.md").write_text("gov", encoding="utf-8")
    (workspace / "knowledge" / "generated").mkdir(parents=True, exist_ok=True)
    (workspace / "knowledge" / "generated" / "AGENT_ANATOMY.md").write_text("anatomy", encoding="utf-8")
    (workspace / "knowledge" / "doctrine" / "ROADMAP.md").write_text("roadmap", encoding="utf-8")
    (workspace / "knowledge" / "maps" / "COMMANDS_MAP.md").write_text("commands", encoding="utf-8")

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal=(
            "corporate model central agent governance subagents self-build "
            "night observation safe autonomy"
        ),
        limit=5,
    )

    assert plan.source_paths == (
        "knowledge/doctrine/future/CORPORATE_MODEL.md",
        "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
        "knowledge/generated/AGENT_ANATOMY.md",
        "knowledge/doctrine/ROADMAP.md",
        "knowledge/maps/COMMANDS_MAP.md",
    )


def test_learning_planner_confidence_goal_prefers_verifier_confidence_sources(
    workspace: Path,
):
    (workspace / "README.md").write_text("overview", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "core" / "planner.py").write_text("planner", encoding="utf-8")
    (workspace / "core" / "verifier.py").write_text("verifier", encoding="utf-8")
    (workspace / "tools" / "file_read.py").write_text("tool", encoding="utf-8")
    (workspace / "tests" / "test_architecture_audit.py").write_text("audit", encoding="utf-8")
    (workspace / "tests" / "test_verifier.py").write_text("verifier tests", encoding="utf-8")
    (workspace / "tests" / "test_evidence_support.py").write_text("gate", encoding="utf-8")
    (workspace / "tests" / "test_confidence_vector.py").write_text("vector", encoding="utf-8")

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="debug low-confidence gate evidence_score verified unverified citations",
        limit=4,
    )

    assert plan.source_paths == (
        "core/verifier.py",
        "tests/test_verifier.py",
        "tests/test_evidence_support.py",
        "tests/test_confidence_vector.py",
    )


def test_learning_planner_mixed_doctrine_confidence_selects_both_layers(
    workspace: Path,
):
    (workspace / "README.md").write_text("overview", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "docs").mkdir()
    (workspace / "knowledge" / "doctrine" / "future").mkdir(parents=True)
    (workspace / "knowledge" / "maps").mkdir(parents=True)
    (workspace / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md").write_text("corp", encoding="utf-8")
    (workspace / "knowledge" / "doctrine" / "CENTRAL_AGENT_GOVERNANCE.md").write_text("gov", encoding="utf-8")
    (workspace / "knowledge" / "generated").mkdir(parents=True, exist_ok=True)
    (workspace / "knowledge" / "generated" / "AGENT_ANATOMY.md").write_text("anatomy", encoding="utf-8")
    (workspace / "knowledge" / "doctrine" / "ROADMAP.md").write_text("roadmap", encoding="utf-8")
    (workspace / "knowledge" / "maps" / "COMMANDS_MAP.md").write_text("commands", encoding="utf-8")
    (workspace / "core" / "verifier.py").write_text("verifier", encoding="utf-8")
    (workspace / "tools" / "shell_exec.py").write_text("tool", encoding="utf-8")
    (workspace / "tests" / "test_verifier.py").write_text("verifier tests", encoding="utf-8")
    (workspace / "tests" / "test_evidence_support.py").write_text("gate", encoding="utf-8")
    (workspace / "tests" / "test_confidence_vector.py").write_text("vector", encoding="utf-8")

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal=(
            "corporate model central agent governance doctrine with "
            "low-confidence evidence citations verifier diagnostics"
        ),
        limit=7,
    )

    assert set(plan.source_paths) == {
        "core/verifier.py",
        "tests/test_verifier.py",
        "tests/test_evidence_support.py",
        "tests/test_confidence_vector.py",
        "knowledge/doctrine/future/CORPORATE_MODEL.md",
        "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
        "knowledge/generated/AGENT_ANATOMY.md",
    }
    assert "README.md" not in plan.source_paths
    assert "tools/shell_exec.py" not in plan.source_paths


def test_learning_planner_local_project_evidence_does_not_force_verifier_sources(
    workspace: Path,
):
    (workspace / "README.md").write_text("overview", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "core" / "evidence.py").write_text("evidence", encoding="utf-8")
    (workspace / "core" / "source_ranker.py").write_text("ranker", encoding="utf-8")
    (workspace / "core" / "verifier.py").write_text("verifier", encoding="utf-8")
    (workspace / "tests" / "test_verifier.py").write_text("verifier tests", encoding="utf-8")
    (workspace / "tests" / "test_evidence_support.py").write_text("gate", encoding="utf-8")
    (workspace / "tests" / "test_confidence_vector.py").write_text("vector", encoding="utf-8")

    goal = "use local project evidence to summarize current project health"
    plan = LearningPlanner().plan(workspace=workspace, goal=goal, limit=3)

    # The property under test is that this goal does NOT trigger the forced
    # confidence-diagnostic source set.
    assert is_confidence_evidence_diagnostic_question(goal) is False
    assert "core/evidence.py" in plan.source_paths
    assert "core/source_ranker.py" in plan.source_paths
    assert "core/verifier.py" not in plan.source_paths
    assert "tests/test_verifier.py" not in plan.source_paths
    assert "tests/test_confidence_vector.py" not in plan.source_paths
    # `tests/test_evidence_support.py` is deliberately NOT asserted absent.
    # After the confidence_gate -> evidence_support rename its name shares the
    # word "evidence" with this goal, so ordinary relevance ranking may pick it
    # — and does, with the reason "matches learning goal". That is selection by
    # relevance, not by forcing, and the assertion above is what distinguishes
    # them. Using a filename as a proxy for "the forced set" is what broke here.


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


def _registry_reading(records: dict[str, float | str]) -> SimpleNamespace:
    """Registry with only get_source: a number is hours since the read, a string is kept as is."""

    def get_source(source_id: str) -> SimpleNamespace | None:
        value = records.get(source_id.removeprefix("file:"))
        if value is None:
            return None
        if not isinstance(value, str):
            value = (datetime.now(timezone.utc) - timedelta(hours=value)).isoformat()
        return SimpleNamespace(last_read_at=value)

    return SimpleNamespace(get_source=get_source)


@pytest.mark.parametrize(
    ("records", "stale_hours"),
    [
        pytest.param({"README.md": 48}, 6.0, id="never-ingested"),
        pytest.param({"core/loop.py": "", "README.md": 48}, 6.0, id="empty-last-read"),
        pytest.param(
            {"core/loop.py": "not-a-real-iso-timestamp", "README.md": 48}, 6.0,
            id="unparseable-last-read",
        ),
        pytest.param({"core/loop.py": 48}, 6.0, id="read-before-the-window"),
        pytest.param({"core/loop.py": -2}, 0.0, id="window-off-read-stamped-ahead"),
    ],
)
def test_apply_staleness_keeps_score_without_a_recent_read(
    workspace: Path, records: dict[str, float | str], stale_hours: float
):
    """core/loop.py (115) stays ahead of README.md (100); the -60 penalty would swap them.

    README's record is a different no-penalty kind, so only loop.py can move.
    """
    (workspace / "core").mkdir()
    (workspace / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (workspace / "README.md").write_text("ov", encoding="utf-8")

    plan = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=_registry_reading(records),
        stale_hours=stale_hours,
    )
    assert plan.source_paths == ("core/loop.py", "README.md")


def test_goal_terms_memory_keyword_picks_memory_files(workspace: Path):
    """The memory term must lift both files over core/loop.py, which ties them at 115 without it."""
    (workspace / "core").mkdir()
    (workspace / "core" / "memory_policy.py").write_text("m", encoding="utf-8")
    (workspace / "core" / "ingestion.py").write_text("i", encoding="utf-8")
    (workspace / "core" / "loop.py").write_text("l", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="памят", limit=2)
    assert set(plan.source_paths) == {"core/memory_policy.py", "core/ingestion.py"}


def test_goal_terms_role_keyword_picks_router(workspace: Path):
    """The role term must lift role_router.py (70+50) over core/loop.py (70; 115 without it)."""
    (workspace / "core").mkdir()
    (workspace / "core" / "role_router.py").write_text("r", encoding="utf-8")
    (workspace / "core" / "loop.py").write_text("l", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="role router", limit=1)
    assert plan.source_paths == ("core/role_router.py",)


def test_goal_terms_tool_keyword_picks_tools(workspace: Path):
    """The tool term must lift tools/shell_exec.py (55+50) over core/loop.py (70; 115 without it)."""
    (workspace / "core").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "tools" / "shell_exec.py").write_text("s", encoding="utf-8")
    (workspace / "core" / "loop.py").write_text("l", encoding="utf-8")

    plan = LearningPlanner().plan(workspace=workspace, goal="инструмент", limit=1)
    assert plan.source_paths == ("tools/shell_exec.py",)


def test_goal_terms_verifier_keyword_picks_verifier(workspace: Path):
    """The verifier term must lift core/verifier.py over core/loop.py when the goal names a path.

    A named path turns the confidence route off; that route picks verifier.py on its own.
    """
    (workspace / "core").mkdir()
    (workspace / "core" / "verifier.py").write_text("v", encoding="utf-8")
    (workspace / "core" / "loop.py").write_text("l", encoding="utf-8")

    goal = "вериф gaps reported in notes.md"
    assert is_confidence_evidence_diagnostic_question(goal) is False
    plan = LearningPlanner().plan(workspace=workspace, goal=goal, limit=1)
    assert plan.source_paths == ("core/verifier.py",)


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
