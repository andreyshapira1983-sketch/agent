"""A weak spot named by reflection must actually be studied.

Reflection asks the model for `focus_area` as a FILE OR MODULE
(`core/reflection.py:375` — 'file or module to focus on (e.g.
"tools/web_fetch.py", "core/memory.py")'), builds the goal
"reflection: study weak areas — <focus_areas>" (`:517`), and the runtime feeds
the resulting plan straight into `ingest_files`
(`core/autonomous_runtime.py:1247`). So whatever the plan picks is literally
what the agent goes and reads.

Measured against the real repository on 2026-08-04, five focus areas of exactly
the shape the prompt asks for — `core/planner.py`, `core/task_queue.py`,
`tools/web_fetch.py`, `core/memory.py`, `core/verifier_core.py`. The named file
was picked **zero times out of five**. Scores for the `core/planner.py` goal:

    core/planner.py            115  = 70 (agent core) + 45 (default spine)
    core/architecture_audit.py 165  = 95 (name says "architecture") + 70

Being named by the goal was worth nothing at all, so a generic filename bonus
won every time and the top of the list was the same for every goal.
"""
from __future__ import annotations

from pathlib import Path

from core.learning_planner import LearningPlanner


def _workspace_with_files(tmp_path: Path, names: list[str]) -> Path:
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n\ncontent of {name}\n", encoding="utf-8")
    return tmp_path


def test_the_file_named_in_the_goal_is_studied(tmp_path: Path):
    """The whole point of the plan: study the weak spot that was named."""
    workspace = _workspace_with_files(tmp_path, [
        "core/planner.py",
        "core/architecture_audit.py",     # generic bonus: "architecture" in name
        "core/evidence.py",
        "README.md",                      # generic bonus: project overview
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/planner.py",
        limit=2,
    )

    assert "core/planner.py" in [p.replace("\\", "/") for p in plan.source_paths]


def test_the_named_file_outranks_the_generic_favourites(tmp_path: Path):
    """Named beats generic, or the plan is the same whatever reflection found."""
    workspace = _workspace_with_files(tmp_path, [
        "core/task_queue.py",
        "core/architecture_audit.py",
        "README.md",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/task_queue.py",
        limit=3,
    )

    assert plan.source_paths[0].replace("\\", "/") == "core/task_queue.py"


def test_several_named_files_are_all_studied(tmp_path: Path):
    """Reflection joins its focus areas with commas; all of them are the point."""
    workspace = _workspace_with_files(tmp_path, [
        "core/planner.py",
        "tools/web_fetch.py",
        "core/architecture_audit.py",
        "README.md",
        "core/evidence.py",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/planner.py, tools/web_fetch.py",
        limit=3,
    )

    picked = [p.replace("\\", "/") for p in plan.source_paths]
    assert "core/planner.py" in picked
    assert "tools/web_fetch.py" in picked


def test_a_named_file_that_does_not_exist_changes_nothing(tmp_path: Path):
    """The model can name a path that is gone; that must not empty the plan."""
    workspace = _workspace_with_files(tmp_path, [
        "core/architecture_audit.py",
        "README.md",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/long_gone.py",
        limit=2,
    )

    assert plan.source_paths, "an unresolvable focus area emptied the plan"


def test_a_goal_naming_no_file_still_picks_the_usual_sources(tmp_path: Path):
    """The operator's `:learn <topic>` path must keep working as before."""
    workspace = _workspace_with_files(tmp_path, [
        "core/memory.py",
        "core/architecture_audit.py",
        "README.md",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="learn how memory works",
        limit=3,
    )

    assert plan.source_paths


def test_a_focus_area_written_as_a_module_is_resolved(tmp_path: Path):
    """The prompt says "file OR MODULE", so `core.memory` must work too."""
    workspace = _workspace_with_files(tmp_path, [
        "core/memory.py",
        "core/architecture_audit.py",
        "README.md",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core.memory",
        limit=2,
    )

    assert plan.source_paths[0].replace("\\", "/") == "core/memory.py"


def test_the_named_file_is_studied_even_from_a_narrowed_root(tmp_path: Path):
    """The `:learn` path rotates `root`; the named file may live outside it.

    Reflection names a weak spot in `core/`, the pass happens to be rooted at
    `tools/`, and the file simply never enters the candidate set — the plan is
    silently about something else.
    """
    workspace = _workspace_with_files(tmp_path, [
        "core/planner.py",
        "tools/web_fetch.py",
        "tools/shell_exec.py",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/planner.py",
        root="tools",
        limit=3,
    )

    picked = [p.replace("\\", "/") for p in plan.source_paths]
    assert "core/planner.py" in picked


def test_a_narrowed_root_still_bounds_everything_else(tmp_path: Path):
    """Only the named file crosses the root — the rest of the pass stays put."""
    workspace = _workspace_with_files(tmp_path, [
        "core/planner.py",
        "core/evidence.py",
        "core/architecture_audit.py",
        "tools/web_fetch.py",
    ])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — core/planner.py",
        root="tools",
        limit=5,
    )

    picked = [p.replace("\\", "/") for p in plan.source_paths]
    assert "core/planner.py" in picked
    assert not [p for p in picked if p.startswith("core/") and p != "core/planner.py"], (
        "files other than the named one leaked in from outside the root"
    )


def test_a_named_path_cannot_reach_outside_the_workspace(tmp_path: Path):
    """A path in the goal is untrusted text: it must not widen what is read."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.md").write_text("not yours", encoding="utf-8")
    workspace = _workspace_with_files(tmp_path / "ws", ["core/planner.py"])

    plan = LearningPlanner().plan(
        workspace=workspace,
        goal="reflection: study weak areas — ../outside/secrets.md",
        limit=5,
    )

    assert all("secrets" not in p for p in plan.source_paths)
