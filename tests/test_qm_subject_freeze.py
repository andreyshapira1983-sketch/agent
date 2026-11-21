"""The subject guard must see both ways the frozen snapshot can move.

The mapping program's claims are all of the form "in S, under domain D". They are
worth nothing if S quietly drifts while the program runs. Two conditions move it:

TRACKED DRIFT -- an existing production file changed.
UNTRACKED ADDITION -- a new file appeared under a production path. This one hides:
every diff of tracked content stays empty, every "no changes" report stays true,
and the agent is nonetheless a different agent.

The guard was bitten by hand against the real repository (both conditions fired,
`main.py` restored byte-identically). These tests make that durable, and run against
a THROWAWAY git repository so the real subject is never mutated to prove a point.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parent.parent / "scripts" / "qm_subject_freeze.py"


def _guard():
    spec = importlib.util.spec_from_file_location("qm_subject_freeze_under_test", _GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A miniature repository with one file under a production path."""
    def run(*argv: str) -> None:
        subprocess.run(  # nosec B603 B607  # noqa: S603
            list(argv), cwd=tmp_path, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.invalid")
    run("git", "config", "user.name", "t")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("lab\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "snapshot")
    sha = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,  # noqa: S607
        capture_output=True, text=True).stdout.strip()
    return tmp_path, sha


def test_an_untouched_subject_is_intact(repo) -> None:
    root, sha = repo
    module = _guard()
    code, evidence = module.check(sha, root)
    assert code == module.INTACT, evidence


def test_a_changed_production_file_is_tracked_drift(repo) -> None:
    root, sha = repo
    module = _guard()
    (root / "core" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    code, evidence = module.check(sha, root)
    assert code == module.TRACKED_DRIFT
    assert any("core/thing.py" in line for line in evidence)


def test_a_new_untracked_module_is_caught_although_no_tracked_file_moved(repo) -> None:
    """The condition an empty `git diff --stat` cannot express."""
    root, sha = repo
    module = _guard()
    (root / "core" / "brand_new.py").write_text("def surprise(): ...\n", encoding="utf-8")
    tracked = subprocess.run(  # nosec B603 B607  # noqa: S603
        ["git", "diff", "--stat", sha], cwd=root, check=True,  # noqa: S607
        capture_output=True, text=True).stdout
    assert tracked.strip() == "", "the premise of this test is that the old guard is blind here"
    code, evidence = module.check(sha, root)
    assert code == module.UNTRACKED_ADDITION
    assert any("brand_new.py" in line for line in evidence)


def test_both_conditions_report_as_both(repo) -> None:
    root, sha = repo
    module = _guard()
    (root / "core" / "thing.py").write_text("VALUE = 3\n", encoding="utf-8")
    (root / "cli").mkdir()
    (root / "cli" / "extra.py").write_text("\n", encoding="utf-8")
    code, _ = module.check(sha, root)
    assert code == module.BOTH


def test_the_laboratory_may_grow_without_moving_the_subject(repo) -> None:
    """`scripts/`, `tests/`, `docs/`, `knowledge/` are the lab, not the agent."""
    root, sha = repo
    module = _guard()
    (root / "docs" / "note.md").write_text("lab, extended\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "qm_new_probe.py").write_text("\n", encoding="utf-8")
    code, evidence = module.check(sha, root)
    assert code == module.INTACT, evidence


def test_an_unusable_snapshot_is_never_reported_as_intact(repo) -> None:
    """A guard that says INTACT when it could not look is worse than no guard."""
    root, _ = repo
    module = _guard()
    code, _ = module.check("0" * 40, root)
    assert code == module.UNREADABLE


def test_the_real_subject_paths_are_the_ones_the_program_froze() -> None:
    module = _guard()
    assert set(module.SUBJECT_PATHS) == {
        "core", "cli", "app", "api", "tools", "agent_tick.py", "main.py"}
