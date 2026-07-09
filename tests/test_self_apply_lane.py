"""Tests for the trusted low-risk self-apply lane (TD-023).

Gate refusals use a lightweight FakeVCS + a runner that raises if called, so we
prove no tests (and therefore no LLM/provider/network) run before apply. The
apply / rollback / commit behaviour is exercised end-to-end against a real
temporary git repo with the real SafeVCS but a fake test runner, so no real
pytest is ever spawned.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.safe_vcs import SafeVCS
from core.self_apply_lane import (
    FileChange,
    SelfApplyProposal,
    classify_patch_risk,
    run_self_apply_lane,
)


# ── fakes ────────────────────────────────────────────────────────────────────


def _pass(passed: int = 5) -> dict:
    return {"exit_code": 0, "timed_out": False, "failed": 0, "errors": 0,
            "passed": passed}


def _fail() -> dict:
    return {"exit_code": 1, "timed_out": False, "failed": 1, "errors": 0,
            "passed": 4}


class FakeRunner:
    def __init__(self, results: list[dict]):
        self.results = list(results)
        self.calls: list[tuple] = []

    def run(self, paths=None, pattern=None) -> dict:
        self.calls.append((paths, pattern))
        return self.results.pop(0)


class RaisingRunner:
    def run(self, paths=None, pattern=None):  # pragma: no cover - must not run
        raise AssertionError("test runner must not run when the lane refuses")


class FakeVCS:
    """Records calls; never touches disk or the network."""

    def __init__(self, *, clean: bool = True, branch: str = "main"):
        self._clean = clean
        self.current = branch
        self.calls: list[tuple] = []
        self.committed_branch: str | None = None

    def current_branch(self) -> str:
        self.calls.append(("current_branch",))
        return self.current

    def is_clean(self) -> bool:
        self.calls.append(("is_clean",))
        return self._clean

    def create_temp_branch(self, name: str) -> None:
        self.calls.append(("create_temp_branch", name))
        self.current = name

    def checkout(self, name: str) -> None:
        self.calls.append(("checkout", name))
        self.current = name

    def delete_branch(self, name: str) -> None:
        self.calls.append(("delete_branch", name))

    def stage_all(self) -> None:
        self.calls.append(("stage_all",))

    def commit(self, message: str) -> str:
        self.calls.append(("commit", message))
        self.committed_branch = self.current
        return "deadbeefcafe"

    def reset_hard(self) -> None:
        self.calls.append(("reset_hard",))

    def clean_untracked(self) -> None:
        self.calls.append(("clean_untracked",))


def _verbs(vcs: FakeVCS) -> set[str]:
    return {c[0] for c in vcs.calls}


def _snapshot(hour: dict | None = None, day: dict | None = None) -> dict:
    return {
        "windows": [
            {"name": "hour", "seconds": 3600, "counters": hour or {}},
            {"name": "day", "seconds": 86400, "counters": day or {}},
        ],
    }


def _proposal(path: str = "core/foo.py", content: str = "x = 2\n") -> SelfApplyProposal:
    return SelfApplyProposal(
        files=(FileChange(path=path, content=content),),
        reason="tweak foo",
        test_paths=("tests/test_foo.py",),
    )


# ── classifier (pure) ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["core/x.py", "cli/y.py", "tools/z.py", "tests/test_a.py", "docs/guide.md",
     "README.md", "docs/sub/notes.md"],
)
def test_classifier_allows_low_risk_paths(path: str):
    ok, _reason, rejected = classify_patch_risk([FileChange(path, "c")])
    assert ok is True
    assert rejected == []


@pytest.mark.parametrize(
    "path",
    [
        "config/budget_limits.json",
        "config/model_registry.json",
        "config/model_catalog.json",
        ".env",
        "secrets/token.py",
        "requirements.txt",
        "poetry.lock",
        ".github/workflows/ci.yml",
        "tools/my_secret.py",
        "id_rsa",
        "core/../config/budget_limits.json",
        "/etc/passwd",
        "core/data.json",
        "run.sh",
    ],
)
def test_classifier_rejects_denylisted_or_non_allowlisted(path: str):
    ok, _reason, rejected = classify_patch_risk([FileChange(path, "c")])
    assert ok is False
    assert rejected == [path]


def test_classifier_rejects_empty_patch():
    ok, _reason, rejected = classify_patch_risk([])
    assert ok is False


# ── gate refusals (no apply, no tests) ───────────────────────────────────────


def test_kill_switch_active_refuses_before_apply(tmp_path: Path):
    vcs = FakeVCS()
    snap = _snapshot(day={"llm_calls": {"used": 999, "limit": 0}})
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs, test_runner=RaisingRunner(),
        budget_snapshot=snap,
    )
    assert report.status == "budget_kill_switch"
    assert "create_temp_branch" not in _verbs(vcs)


def test_low_budget_refuses_before_apply(tmp_path: Path):
    vcs = FakeVCS()
    snap = _snapshot(hour={"llm_calls": {"used": 19, "limit": 20}})
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs, test_runner=RaisingRunner(),
        budget_snapshot=snap,
    )
    assert report.status == "budget_wait"
    assert "create_temp_branch" not in _verbs(vcs)


def test_pending_approval_refuses_before_apply(tmp_path: Path):
    vcs = FakeVCS()
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs, test_runner=RaisingRunner(),
        budget_snapshot=_snapshot(), approvals_pending=2,
    )
    assert report.status == "approval_wait"
    assert "create_temp_branch" not in _verbs(vcs)


def test_denylisted_file_rejected_before_apply(tmp_path: Path):
    vcs = FakeVCS()
    proposal = SelfApplyProposal(
        files=(FileChange("config/budget_limits.json", '{"x": 1}'),),
    )
    report = run_self_apply_lane(
        proposal, workspace=tmp_path, vcs=vcs, test_runner=RaisingRunner(),
        budget_snapshot=_snapshot(),
    )
    assert report.status == "rejected"
    assert "config/budget_limits.json" in report.rejected_files
    assert "create_temp_branch" not in _verbs(vcs)


def test_dirty_workspace_rejected(tmp_path: Path):
    vcs = FakeVCS(clean=False)
    report = run_self_apply_lane(
        _proposal(), workspace=tmp_path, vcs=vcs, test_runner=RaisingRunner(),
        budget_snapshot=_snapshot(),
    )
    assert report.status == "rejected"
    assert "create_temp_branch" not in _verbs(vcs)


def test_no_push_method_anywhere():
    for forbidden in ("push", "fetch", "pull", "remote"):
        assert not hasattr(SafeVCS, forbidden)
        assert not hasattr(FakeVCS, forbidden)


# ── end-to-end against a real temp git repo (fake test runner) ───────────────


def _git(ws: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", *args],
        cwd=str(ws), check=True, capture_output=True, text=True,
    )


def _init_repo(ws: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(ws), check=True, capture_output=True,
    )
    (ws / "core").mkdir(parents=True, exist_ok=True)
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "core" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "tests" / "test_foo.py").write_text("def test_x():\n    assert True\n",
                                              encoding="utf-8")
    (ws / "config" / "budget_limits.json").write_text('{"keep": true}\n',
                                                      encoding="utf-8")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "init")


def _head(ws: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=str(ws), capture_output=True, text=True,
    ).stdout.strip()


def _show(ws: Path, ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=str(ws), capture_output=True,
        text=True,
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


def test_low_risk_patch_applied_and_committed_locally(repo: Path):
    vcs = SafeVCS(workspace=repo)
    main_head_before = _head(repo, "main")
    runner = FakeRunner([_pass(), _pass()])

    report = run_self_apply_lane(
        _proposal(content="x = 42\n"), workspace=repo, vcs=vcs,
        test_runner=runner, budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local"
    assert report.commit_hash
    assert report.tests_run == ["targeted", "full"]
    assert report.branch and report.branch.startswith("self-apply/")
    # Back on main, which is untouched; the change lives on the temp branch.
    assert vcs.current_branch() == "main"
    assert _head(repo, "main") == main_head_before
    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"
    assert _show(repo, report.branch, "core/foo.py") == "x = 42\n"
    # budget_limits.json is never touched.
    assert (repo / "config" / "budget_limits.json").read_text(encoding="utf-8") == \
        '{"keep": true}\n'


def test_failing_targeted_tests_trigger_rollback(repo: Path):
    vcs = SafeVCS(workspace=repo)
    runner = FakeRunner([_fail()])  # targeted fails; full never runs

    report = run_self_apply_lane(
        _proposal(content="x = 42\n"), workspace=repo, vcs=vcs,
        test_runner=runner, budget_snapshot=_snapshot(),
    )

    assert report.status == "rolled_back"
    assert report.rollback_status == "restored"
    assert report.tests_run == ["targeted"]
    assert len(runner.calls) == 1  # full suite skipped
    assert vcs.current_branch() == "main"
    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"
    branches = subprocess.run(
        ["git", "branch"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert "self-apply/" not in branches


def test_failing_full_suite_triggers_rollback(repo: Path):
    vcs = SafeVCS(workspace=repo)
    runner = FakeRunner([_pass(), _fail()])  # targeted green, full red

    report = run_self_apply_lane(
        _proposal(content="x = 42\n"), workspace=repo, vcs=vcs,
        test_runner=runner, budget_snapshot=_snapshot(),
    )

    assert report.status == "rolled_back"
    assert report.rollback_status == "restored"
    assert report.tests_run == ["targeted", "full"]
    assert vcs.current_branch() == "main"
    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == "x = 1\n"


def test_main_branch_not_modified_on_success(repo: Path):
    vcs = SafeVCS(workspace=repo)
    main_before = _head(repo, "main")
    runner = FakeRunner([_pass(), _pass()])

    report = run_self_apply_lane(
        _proposal(content="x = 7\n"), workspace=repo, vcs=vcs,
        test_runner=runner, budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local"
    assert _head(repo, "main") == main_before  # main HEAD unchanged
    assert _head(repo, report.branch) != main_before  # commit on temp branch


def test_new_file_is_cleaned_on_rollback(repo: Path):
    vcs = SafeVCS(workspace=repo)
    runner = FakeRunner([_fail()])
    proposal = SelfApplyProposal(
        files=(FileChange("core/brand_new.py", "y = 9\n"),),
        reason="add new module",
        test_paths=("tests/test_foo.py",),
    )

    report = run_self_apply_lane(
        proposal, workspace=repo, vcs=vcs, test_runner=runner,
        budget_snapshot=_snapshot(),
    )

    assert report.status == "rolled_back"
    assert report.rollback_status == "restored"
    assert not (repo / "core" / "brand_new.py").exists()
