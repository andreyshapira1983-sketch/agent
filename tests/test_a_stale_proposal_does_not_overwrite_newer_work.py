"""Предложение недельной давности не вправе затирать сегодняшнюю работу.

Замер, отвергнутые варианты и границы: MIR-168 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.safe_vcs import SafeVCS
from core.self_apply_bridge import build_self_apply_payload, rehydrate_proposal
from core.self_apply_lane import run_self_apply_lane
from tests.test_self_apply_lane import (
    FakeRunner,
    RaisingRunner,
    _git,
    _init_repo,
    _pass,
    _snapshot,
)

_BEFORE = "x = 1\n"
_PROPOSED = "x = 2\n"
_NEWER = "x = 3  # работа, сделанная после подачи предложения\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Репозиторий, где `core/foo.py` уже лежит и закоммичен как `_BEFORE`."""
    _init_repo(tmp_path)
    return tmp_path


def _commit_newer(repo: Path) -> None:
    """Чужая работа, СДЕЛАННАЯ И ЗАКОММИЧЕННАЯ после подачи предложения.

    Именно закоммиченная: незакоммиченную ловят пятые ворота (чистота дерева),
    и дыра не там. Уязвим случай, когда дерево чистое, а файл уже другой.
    """
    (repo / "core" / "foo.py").write_text(_NEWER, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "newer work")


def _payload(repo: Path, *, workspace: Path | None):
    return build_self_apply_payload(
        files=[{"path": "core/foo.py", "content": _PROPOSED}],
        reason="починка foo",
        test_paths=("tests/test_foo.py",),
        workspace=workspace,
    )


def test_a_proposal_refuses_when_its_target_changed_since(repo: Path) -> None:
    """Красный свидетель: старый пост-образ пишется поверх нового молча.

    Полоса переписывает файлы ЦЕЛИКОМ (`_write_file`), а из пяти ворот ни одни
    не спрашивают, тот ли файл, на котором предложение построено. Чистота
    дерева — про незакоммиченные правки, а не про версию. Срок заявки сутки,
    и в этом окне откат проходит зелёным везде, где на файл нет теста: все три
    исполненных предложения в живом ящике целили в `knowledge/doctrine/`.
    """
    payload = _payload(repo, workspace=repo)
    _commit_newer(repo)

    report = run_self_apply_lane(
        rehydrate_proposal(payload), workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=RaisingRunner(), budget_snapshot=_snapshot(),
    )

    assert report.status == "stale_proposal"
    assert (repo / "core" / "foo.py").read_text(encoding="utf-8") == _NEWER, (
        "предложение затёрло работу, сделанную после его подачи"
    )


def test_an_unchanged_target_still_applies(repo: Path) -> None:
    """Контроль: сторож не должен запирать нормальный путь."""
    payload = _payload(repo, workspace=repo)

    report = run_self_apply_lane(
        rehydrate_proposal(payload), workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=FakeRunner([_pass(), _pass()]), budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local"


def test_a_brand_new_file_has_no_base_to_match(repo: Path) -> None:
    """Файла ещё нет — сверять не с чем, и это не повод отказывать."""
    payload = build_self_apply_payload(
        files=[{"path": "core/brand_new.py", "content": "y = 1\n"}],
        test_paths=("tests/test_foo.py",),
        workspace=repo,
    )

    report = run_self_apply_lane(
        rehydrate_proposal(payload), workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=FakeRunner([_pass(), _pass()]), budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local"


def test_an_unrecorded_base_is_named_not_assumed_to_match(repo: Path) -> None:
    """«Отметки нет» — это НЕ «отметка сошлась».

    Заявки, поданные до этой правки, отметки не несут; они истекут за сутки.
    Поведение для них прежнее — но молчать об этом нельзя, иначе непроверенное
    выдаётся за проверенное.
    """
    payload = _payload(repo, workspace=None)
    _commit_newer(repo)

    report = run_self_apply_lane(
        rehydrate_proposal(payload), workspace=repo, vcs=SafeVCS(workspace=repo),
        test_runner=FakeRunner([_pass(), _pass()]), budget_snapshot=_snapshot(),
    )

    assert report.status == "committed_local"
    assert "base_unverified" in report.risks


def test_the_live_producer_actually_stamps_the_base(repo: Path) -> None:
    """Проводка, а не только сторож: боевой публикатор снимает отметку.

    Без этого сторож стоял бы мёртвым — отметки не появлялось бы никогда, и
    ворота не срабатывали бы ни разу. Ровно так уже случалось (MIR-138).
    """
    from core.approval_inbox import ApprovalInbox
    from core.self_build_producer import _reporter_publish

    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")
    _reporter_publish(
        inbox,
        "core/foo.py",
        {"content": _PROPOSED, "test_paths": ["tests/test_foo.py"]},
        ["улика"],
        repo,
    )

    filed = inbox.list()[-1].payload["files"][0]
    assert filed["base_checked"] is True
    assert filed["base_sha256"], "отметка снята пустой — сверять будет нечем"
