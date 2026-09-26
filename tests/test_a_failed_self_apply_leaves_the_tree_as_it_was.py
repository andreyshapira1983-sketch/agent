"""Сбой полосы самоправки на ветке, записи или коммите оставляет дерево и ветку как были."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import core.self_apply_lane as lane
from core.safe_vcs import SafeVCS, VcsError
from core.self_apply_lane import FileChange, SelfApplyProposal, run_self_apply_lane
from tests.test_self_apply_lane import FakeRunner, _init_repo, _pass, _snapshot


def _tree(ws: Path) -> dict[str, bytes]:
    return {
        p.relative_to(ws).as_posix(): p.read_bytes()
        for p in sorted(ws.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(ws).parts
    }


def _branches(ws: Path) -> list[str]:
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=ws,  # noqa: S607
                         check=True, capture_output=True, text=True).stdout
    return sorted(out.split())


def _proposal() -> SelfApplyProposal:
    return SelfApplyProposal(
        files=(FileChange("core/foo.py", "x = 2\n"), FileChange("core/new_mod.py", "y = 3\n")),
        reason="tweak foo",
        test_paths=("tests/test_foo.py",),
    )


def _run(ws: Path, vcs: SafeVCS):
    return run_self_apply_lane(_proposal(), workspace=ws, vcs=vcs,
                               test_runner=FakeRunner([_pass(), _pass()]), budget_snapshot=_snapshot())


class _NoBranchVCS(SafeVCS):
    def create_temp_branch(self, name: str) -> None:
        raise VcsError("simulated: cannot create branch")


class _NoCommitVCS(SafeVCS):
    def commit(self, message: str) -> str:
        raise VcsError("simulated: commit failed")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


def test_a_branch_that_cannot_be_created_is_an_error_and_touches_nothing(repo: Path) -> None:
    """Не создалась временная ветка — ошибка, и ни дерево, ни ветки не тронуты."""
    tree, branches = _tree(repo), _branches(repo)
    vcs = _NoBranchVCS(workspace=repo)

    report = _run(repo, vcs)

    assert report.status == "error", report
    assert "temp branch" in report.reason
    assert _tree(repo) == tree
    assert _branches(repo) == branches
    assert vcs.current_branch() == "main"


def test_a_failed_file_write_rolls_the_tree_back(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой записи посреди правки откатывает уже записанное: дерево байт в байт как до попытки."""
    tree, branches = _tree(repo), _branches(repo)
    real_write = lane._write_file
    written: list[str] = []

    def _write_then_fail(workspace: Path, rel: str, content: str) -> None:
        if written:
            raise OSError("simulated: disk full")
        real_write(workspace, rel, content)
        written.append(rel)

    monkeypatch.setattr(lane, "_write_file", _write_then_fail)
    vcs = SafeVCS(workspace=repo)

    report = _run(repo, vcs)

    assert written == ["core/foo.py"], "первый файл должен успеть записаться"
    assert report.status == "rolled_back", report
    assert report.rollback_status == "restored"
    assert _tree(repo) == tree
    assert _branches(repo) == branches
    assert vcs.current_branch() == "main"


def test_a_failed_commit_rolls_the_tree_back(repo: Path) -> None:
    """Сбой коммита после зелёных тестов откатывает правку и возвращает исходную ветку."""
    tree, branches = _tree(repo), _branches(repo)
    vcs = _NoCommitVCS(workspace=repo)

    report = _run(repo, vcs)

    assert report.status == "rolled_back", report
    assert "commit failed" in report.reason
    assert report.rollback_status == "restored"
    assert _tree(repo) == tree
    assert _branches(repo) == branches
    assert vcs.current_branch() == "main"
