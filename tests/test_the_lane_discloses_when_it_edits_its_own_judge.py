"""Патч, меняющий политику и её судью одним актом, обязан сказать об этом.

Замер, отвергнутые варианты и границы: MIR-139 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.safe_vcs import SafeVCS
from core.self_apply_lane import FileChange, SelfApplyProposal, run_self_apply_lane

_POLICY = "core/foo.py"
_JUDGE = "tests/test_foo.py"

_JUDGE_BEFORE = (
    "def test_x():\n"
    "    assert True\n"
    "\n"
    "def test_y():\n"
    "    assert 1 == 1\n"
    "    assert 2 == 2\n"
)
_JUDGE_WEAKENED = "def test_x():\n    assert True\n"
_JUDGE_STRENGTHENED = _JUDGE_BEFORE + "\ndef test_z():\n    assert 3 == 3\n"


def _git(ws: Path, *args: str) -> None:
    # Собственная личность полигона: на CI-раннере нет user.email/user.name,
    # и `git commit` падал кодом 128 (три ERRORS на каждом прогоне GitHub,
    # дома невидимо — локальный git настроен). 2026-08-28.
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=polygon@test", "-c", "user.name=polygon",  # noqa: S607
         *args], cwd=str(ws), check=True, capture_output=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init"], cwd=str(tmp_path), check=True, capture_output=True  # noqa: S607
    )
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],  # noqa: S607
        cwd=str(tmp_path), check=True, capture_output=True,
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / _JUDGE).write_text(_JUDGE_BEFORE, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _snapshot() -> dict:
    return {"windows": [
        {"name": "hour", "seconds": 3600, "counters": {}},
        {"name": "day", "seconds": 86400, "counters": {}},
    ]}


class _PassingRunner:
    """Зелёный прогон — ровно та ситуация, ради которой запись заведена.

    Патч, ослабивший судью, ДОЛЖЕН давать зелёный набор: в этом и состоит
    беда. Поддельный бегун здесь не упрощение, а воспроизведение механизма.
    """

    def run(self, paths=None, pattern=None) -> dict:
        return {"exit_code": 0, "timed_out": False, "failed": 0,
                "errors": 0, "passed": 5}


def _run(repo: Path, judge_after: str):
    return run_self_apply_lane(
        SelfApplyProposal(
            files=(
                FileChange(path=_POLICY, content="x = 42\n"),
                FileChange(path=_JUDGE, content=judge_after),
            ),
            reason="правка политики вместе с её тестом",
            test_paths=(_JUDGE,),
        ),
        workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=_PassingRunner(), budget_snapshot=_snapshot(),
    )


def test_weakening_the_judge_alongside_the_policy_is_disclosed(repo: Path) -> None:
    report = _run(repo, _JUDGE_WEAKENED)

    assert report.status == "committed_local", report.reason
    blob = " ".join(report.risks)
    assert "judge" in blob.lower(), (
        "патч изменил политику и ослабил её тест одним актом, а отчёт человеку "
        "об этом молчит:\n" + blob
    )
    assert _JUDGE in blob, "риск назван, но не сказано, какой судья изменён: " + blob
    assert "2" in blob and "1" in blob, (
        "не названы числа до и после — человеку нечем взвесить: " + blob
    )


def test_strengthening_the_judge_is_not_flagged(repo: Path) -> None:
    """Граница: добавленный тест поднять самооценку не может.

    Без неё правило шумело бы на каждой честной починке, а шумящее
    предупреждение перестают читать — и оно становится хуже отсутствующего.
    """
    report = _run(repo, _JUDGE_STRENGTHENED)

    assert report.status == "committed_local", report.reason
    assert not [r for r in report.risks if "judge" in r.lower()], report.risks


def test_a_policy_only_patch_is_not_flagged(repo: Path) -> None:
    """Граница: без правки судьи говорить не о чем."""
    report = run_self_apply_lane(
        SelfApplyProposal(
            files=(FileChange(path=_POLICY, content="x = 7\n"),),
            reason="только политика",
            test_paths=(_JUDGE,),
        ),
        workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=_PassingRunner(), budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local", report.reason
    assert not [r for r in report.risks if "judge" in r.lower()], report.risks

# ── и доходит ли раскрытие ДО человека ───────────────────────────────────────


def test_the_risk_reaches_the_operators_screen(tmp_path, capsys, monkeypatch) -> None:
    """Поле заполнено — этого мало; человек обязан его УВИДЕТЬ.

    Замер 2026-08-24: команда печатала proposal, status, reason, branch,
    files_changed, rollback_status, commit, rejected_files и next — и НЕ
    печатала `risks`. Раскрытие, до которого не доходит взгляд, равно молчанию;
    сегодня этот урок повторился трижды, и здесь он проверяется отдельным
    тестом, а не надеждой.
    """
    import cli.commands_self_apply as mod

    class _Log:
        def log(self, *a, **k) -> None:  # команде нужен только он
            return None

    class _Agent:
        log = _Log()

    agent = _Agent()

    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: "INBOX")
    monkeypatch.setattr(mod, "SafeVCS", lambda workspace: "VCS")
    monkeypatch.setattr(mod, "RunTestsTool", lambda workspace_root: "RUNNER")
    monkeypatch.setattr(mod, "BudgetKillSwitch", lambda path: "KILL")
    monkeypatch.setattr(mod, "default_path", lambda ws: ws / "kill.json")
    monkeypatch.setattr(mod, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        mod, "record_self_build_episode", lambda agent, *, kind, result: None
    )
    risk = (
        "judge tests/test_foo.py was WEAKENED in the same patch as policy "
        "(core/foo.py): tests 2 -> 1, asserts 3 -> 1"
    )
    monkeypatch.setattr(mod, "run_approved_self_apply", lambda **k: {
        "proposal_id": "ap-1", "status": "committed_local", "reason": "green",
        "branch": "self-apply/ap-1", "files_changed": ["core/foo.py"],
        "rollback_status": "none", "commit_hash": "abc1234",
        "risks": [risk], "next_human_action": "review the branch",
    })

    mod._handle_self_apply_run("ap-1", agent, tmp_path)
    printed = capsys.readouterr().err

    assert "WEAKENED" in printed, (
        "риск посчитан и записан в отчёт, но на экран не выведен — человек "
        "решает о слиянии, не видя, что судья изменился: " + printed
    )
    assert "tests/test_foo.py" in printed
