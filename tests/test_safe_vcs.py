"""Tests for the narrow safe VCS helper (TD-023)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.safe_vcs import SafeVCS, VcsError, _validate_branch


def _git(ws: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", *args],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(ws: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(ws), check=True, capture_output=True,
    )
    (ws / "core").mkdir(parents=True, exist_ok=True)
    (ws / "core" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "init")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


def test_current_branch_and_clean(repo: Path):
    vcs = SafeVCS(workspace=repo)
    assert vcs.current_branch() == "main"
    assert vcs.is_clean() is True


def test_dirty_tree_detected(repo: Path):
    (repo / "core" / "foo.py").write_text("x = 2\n", encoding="utf-8")
    assert SafeVCS(workspace=repo).is_clean() is False


def test_create_branch_commit_and_checkout(repo: Path):
    vcs = SafeVCS(workspace=repo)
    main_head = vcs.head_hash()
    vcs.create_temp_branch("self-apply/t1")
    assert vcs.current_branch() == "self-apply/t1"

    (repo / "core" / "foo.py").write_text("x = 42\n", encoding="utf-8")
    vcs.stage_all()
    commit_hash = vcs.commit("tweak foo")
    assert commit_hash and commit_hash != main_head

    vcs.checkout("main")
    # main is untouched by the temp-branch commit.
    assert vcs.head_hash() == main_head
    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"


def test_reset_hard_and_clean_untracked(repo: Path):
    vcs = SafeVCS(workspace=repo)
    vcs.create_temp_branch("self-apply/t2")
    (repo / "core" / "foo.py").write_text("broken\n", encoding="utf-8")
    (repo / "core" / "new_file.py").write_text("new\n", encoding="utf-8")

    vcs.reset_hard()
    vcs.clean_untracked()

    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (repo / "core" / "new_file.py").exists()


def test_delete_branch(repo: Path):
    vcs = SafeVCS(workspace=repo)
    vcs.create_temp_branch("self-apply/t3")
    vcs.checkout("main")
    vcs.delete_branch("self-apply/t3")
    branches = subprocess.run(
        ["git", "branch"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert "self-apply/t3" not in branches


def test_refuses_to_delete_protected_branch(repo: Path):
    with pytest.raises(VcsError):
        SafeVCS(workspace=repo).delete_branch("main")


@pytest.mark.parametrize("bad", ["../evil", "a b", "-x", "", "foo/../bar", "a;b"])
def test_invalid_branch_names_rejected(bad: str):
    with pytest.raises(VcsError):
        _validate_branch(bad)


def test_failed_git_command_raises(repo: Path):
    with pytest.raises(VcsError):
        SafeVCS(workspace=repo).checkout("does-not-exist")


def test_no_network_methods_exist():
    # The helper must not expose push/fetch/pull/remote at all.
    for forbidden in ("push", "fetch", "pull", "remote"):
        assert not hasattr(SafeVCS, forbidden)
