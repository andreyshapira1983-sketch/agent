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
    RaisingRunner,
    _git,
    _init_repo,
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
