"""Regression test: app directory TODO/FIXME/XXX markers are discoverable.

Verifies that the backlog selector scans app/*.py files for grounded TODO/FIXME/XXX
markers and surfaces them as backlog candidates (TD-036 Phase 1 self-inspection).
"""
from pathlib import Path
from textwrap import dedent

from core.backlog_selector import load_backlog


def test_app_directory_todos_are_discovered(tmp_path: Path) -> None:
    """When app/*.py contains a TODO comment, load_backlog() yields a candidate."""
    # Arrange: minimal workspace with a single app Python file containing a TODO
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    app_file = app_dir / "example_module.py"
    app_file.write_text(
        dedent(
            """
            # Example app module
            def some_function():
                # TODO: refactor this function for clarity
                pass
            """
        ),
        encoding="utf-8",
    )

    # Act: load the backlog from the workspace
    candidates = load_backlog(tmp_path)

    # Assert: at least one candidate originates from the app file
    app_candidates = [
        c for c in candidates
        if c.target_path == "app/example_module.py"
        and c.signal_source == "code_todo"
    ]
    assert len(app_candidates) > 0, (
        "Expected at least one backlog candidate from app/example_module.py, "
        f"but found none. All candidates: {[c.target_path for c in candidates]}"
    )

    # Assert: the candidate quotes the actual TODO comment
    candidate = app_candidates[0]
    assert "TODO" in candidate.problem_quote, (
        f"Expected problem_quote to contain 'TODO', got: {candidate.problem_quote}"
    )
    assert "refactor this function for clarity" in candidate.problem_quote, (
        f"Expected problem_quote to reference the TODO text, got: {candidate.problem_quote}"
    )


def test_app_subdirectory_todos_are_discovered(tmp_path: Path) -> None:
    """When app/subdir/*.py contains a FIXME, load_backlog() yields a candidate."""
    # Arrange: app subdirectory with a FIXME marker
    subdir = tmp_path / "app" / "handlers"
    subdir.mkdir(parents=True)
    handler_file = subdir / "handler.py"
    handler_file.write_text(
        dedent(
            """
            # FIXME: improve error handling in this handler
            def handle_request():
                pass
            """
        ),
        encoding="utf-8",
    )

    # Act
    candidates = load_backlog(tmp_path)

    # Assert: candidate from the nested app file exists
    nested_candidates = [
        c for c in candidates
        if c.target_path == "app/handlers/handler.py"
        and c.signal_source == "code_todo"
    ]
    assert len(nested_candidates) > 0, (
        "Expected candidate from app/handlers/handler.py, found none"
    )
    assert "FIXME" in nested_candidates[0].problem_quote


def test_app_todos_absent_when_no_app_directory(tmp_path: Path) -> None:
    """When workspace has no app directory, load_backlog() degrades gracefully."""
    # Arrange: workspace with only core (no app)
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "example.py").write_text(
        "# TODO: core task\npass\n",
        encoding="utf-8",
    )

    # Act: should not raise, even though app is missing
    candidates = load_backlog(tmp_path)

    # Assert: core candidate exists, no app candidates (none to find)
    core_candidates = [c for c in candidates if "core/example.py" in c.target_path]
    app_candidates = [c for c in candidates if c.target_path.startswith("app/")]
    assert len(core_candidates) > 0, "Expected core TODO to be found"
    assert len(app_candidates) == 0, "Expected no app candidates when app dir missing"
