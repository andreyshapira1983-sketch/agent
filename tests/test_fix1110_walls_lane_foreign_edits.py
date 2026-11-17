"""Откат полосы самоправки не стирает чужую правку, а сама полоса идёт под замком."""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

import core.self_apply_lane as lane
from core.safe_vcs import SafeVCS
from core.self_apply_lane import FileChange, SelfApplyProposal, run_self_apply_lane
from tests.test_self_apply_lane import FakeRunner, FakeVCS, _fail, _init_repo, _pass, _snapshot


def _out(ws: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ws), check=True,  # noqa: S603, S607
                          capture_output=True, text=True).stdout.strip()


def _proposal() -> SelfApplyProposal:
    return SelfApplyProposal(
        files=(FileChange("core/foo.py", "x = 2\n"), FileChange("core/new_mod.py", "y = 3\n")),
        reason="tweak foo",
        test_paths=("tests/test_foo.py",),
    )


class _RunnerWithForeignEdit(FakeRunner):
    """Пока идут тесты, кто-то другой правит дерево."""

    def __init__(self, results: list[dict], ws: Path):
        super().__init__(results)
        self._ws = ws

    def run(self, paths=None, pattern=None) -> dict:
        (self._ws / "tests" / "test_foo.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
        (self._ws / "operator_notes.txt").write_text("не стирать\n", encoding="utf-8")
        return super().run(paths=paths, pattern=pattern)


def test_a_red_run_keeps_edits_the_lane_did_not_make(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=SafeVCS(workspace=tmp_path),
        test_runner=_RunnerWithForeignEdit([_fail()], tmp_path), budget_snapshot=_snapshot(),
    )
    assert report.status == "rolled_back"
    assert report.rollback_status == "kept_foreign_changes"
    # чужое на месте
    assert (tmp_path / "operator_notes.txt").read_text(encoding="utf-8") == "не стирать\n"
    assert "assert 1" in (tmp_path / "tests" / "test_foo.py").read_text(encoding="utf-8")
    # своё убрано, ветка прежняя
    assert (tmp_path / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "core" / "new_mod.py").exists()
    assert _out(tmp_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert report.branch not in _out(tmp_path, "branch")


def test_a_red_run_without_foreign_edits_restores_the_tree_as_before(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=SafeVCS(workspace=tmp_path),
        test_runner=FakeRunner([_fail()]), budget_snapshot=_snapshot(),
    )
    assert report.rollback_status == "restored"
    assert (tmp_path / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / "core" / "new_mod.py").exists()
    assert _out(tmp_path, "status", "--porcelain") == ""


def test_the_lane_runs_under_a_lock_outside_the_tree(tmp_path: Path, monkeypatch) -> None:
    seen: list[Path] = []

    @contextmanager
    def fake_lock(path):
        seen.append(Path(path))
        yield

    monkeypatch.setattr(lane, "exclusive_file_lock", fake_lock)
    vcs = FakeVCS()
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs,
        test_runner=FakeRunner([_pass(), _pass()]), budget_snapshot=_snapshot(),
    )
    assert report.status == "committed_local"
    assert len(seen) == 1
    assert tmp_path.resolve() not in seen[0].resolve().parents


def test_two_workspaces_do_not_share_a_lock(tmp_path: Path) -> None:
    assert lane._lane_lock_path(tmp_path / "a") != lane._lane_lock_path(tmp_path / "b")
    assert lane._lane_lock_path(tmp_path / "a") == lane._lane_lock_path(tmp_path / "a" / "." )


def test_a_busy_lock_is_a_refusal_that_touches_nothing(tmp_path: Path, monkeypatch) -> None:
    @contextmanager
    def busy_lock(path):
        raise OSError("lock held")
        yield

    monkeypatch.setattr(lane, "exclusive_file_lock", busy_lock)
    vcs = FakeVCS()
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs,
        test_runner=FakeRunner([]), budget_snapshot=_snapshot(),
    )
    assert report.status == "error"
    assert vcs.calls == []
    assert not (tmp_path / "core").exists()
