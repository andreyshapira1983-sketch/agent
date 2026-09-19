"""Recovery must be authorised by the journal, and must not exceed it.

The mapping program's restore invariant used to live only in model context: a
turn cut between the mutation and the `git checkout` would have left production
mutated with the baseline hash unrecoverable. Twenty applications survived on the
fact that no turn was ever cut.

The operator's correction has two halves and they are not symmetric, which is the
whole reason this file exists:

  authorised    a path recorded in an OPEN entry whose baseline is mechanically
                tied to the snapshot is restored and verified by hash.
  unauthorised  drift NOT attributable to that entry is an INTEGRITY CONFLICT.
                It is reported and LEFT ALONE. A recovery tool that deletes an
                unexplained change destroys the evidence that something else is
                happening, which is worse than the drift.

Everything below runs against a throwaway git repository. The real frozen subject
is never touched to prove a point about touching it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "scripts" / "qm_mutation_journal.py"


def _load(root: Path, snapshot: str):
    spec = importlib.util.spec_from_file_location("qm_mutation_journal_under_test", _TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.ROOT = root
    module.JOURNAL = root / "lab" / "MUTATION_JOURNAL.json"
    module.JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    module.SNAPSHOT = snapshot
    module.SUBJECT_PATHS = ("core",)
    module._RUN = module.functools.partial(  # the tool's own seam
        subprocess.run, cwd=root, capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace", timeout=120)
    return module


@pytest.fixture
def repo(tmp_path: Path):
    def run(*argv: str) -> None:
        subprocess.run(  # nosec B603 B607  # noqa: S603
            list(argv), cwd=tmp_path, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.invalid")
    run("git", "config", "user.name", "t")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "core" / "b.py").write_text("B = 1\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "snapshot")
    sha = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,  # noqa: S607
        capture_output=True, text=True).stdout.strip()
    return _load(tmp_path, sha), tmp_path


def test_a_clean_tree_has_no_open_entry(repo) -> None:
    module, _root = repo
    assert module.journal("status")[0] == module.CLEAN


def test_opening_refuses_a_path_that_already_drifted(repo) -> None:
    """An entry whose baseline is not the snapshot could not authorise recovery."""
    module, root = repo
    (root / "core" / "a.py").write_text("A = 999\n", encoding="utf-8")
    code, lines = module.journal("open", ("core/a.py",))
    assert code == module.CONFLICT
    assert any("already differs" in line for line in lines)


def test_only_one_experiment_may_be_open(repo) -> None:
    module, _root = repo
    assert module.journal("open", ("core/a.py",))[0] == module.OPEN
    assert module.journal("open", ("core/b.py",))[0] == module.CONFLICT


def test_closing_is_refused_while_the_path_is_still_mutated(repo) -> None:
    module, root = repo
    module.journal("open", ("core/a.py",))
    (root / "core" / "a.py").write_text("A = 2\n", encoding="utf-8")
    assert module.journal("close")[0] == module.CONFLICT
    assert (root / "core" / "a.py").read_text(encoding="utf-8") == "A = 2\n", (
        "a refused close must not quietly restore anything"
    )


def test_recovery_restores_exactly_what_the_entry_authorises(repo) -> None:
    module, root = repo
    module.journal("open", ("core/a.py",))
    (root / "core" / "a.py").write_text("A = 2\n", encoding="utf-8")

    code, lines = module.journal("recover")

    assert code == module.RESTORED, lines
    assert (root / "core" / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert not module.JOURNAL.exists(), "a fully restored entry must close itself"


def test_unexplained_drift_is_reported_and_left_alone(repo) -> None:
    """The half that matters: recovery may not exceed its authorisation."""
    module, root = repo
    module.journal("open", ("core/a.py",))
    (root / "core" / "a.py").write_text("A = 2\n", encoding="utf-8")      # authorised
    (root / "core" / "b.py").write_text("B = 2\n", encoding="utf-8")      # NOT
    (root / "core" / "stray.py").write_text("# stray\n", encoding="utf-8")  # NOT

    code, lines = module.journal("recover")

    assert code == module.CONFLICT
    assert (root / "core" / "a.py").read_text(encoding="utf-8") == "A = 1\n", (
        "the authorised path must still be restored"
    )
    assert (root / "core" / "b.py").read_text(encoding="utf-8") == "B = 2\n", (
        "an unexplained modification was destroyed — that is the failure this "
        "test exists for"
    )
    assert (root / "core" / "stray.py").exists(), (
        "an unexplained untracked file under the subject was destroyed"
    )
    assert any("INTEGRITY CONFLICT" in line for line in lines)


def test_recovery_on_a_clean_tree_with_no_entry_does_nothing(repo) -> None:
    """GUARD: the assertions above must not be satisfied by a tool that no-ops."""
    module, root = repo
    code, _lines = module.journal("recover")
    assert code == module.CLEAN
    assert (root / "core" / "a.py").read_text(encoding="utf-8") == "A = 1\n"
