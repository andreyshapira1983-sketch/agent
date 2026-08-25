"""Откат не может снести живое состояние, улики и историю.

Замер, отвергнутые варианты и границы: H-36 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, H-12 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pathlib

import pytest

from core.compensation import CompensationAction, _apply_action


def _workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    for name in ("data", "logs", ".git", "config", "core", "tests"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "живое.txt").write_text("важное", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("target", ["data", "logs", ".git", "config", "core", "tests"])
def test_a_protected_root_is_never_removed(tmp_path, target: str) -> None:
    ws = _workspace(tmp_path)

    outcome = _apply_action(
        CompensationAction(kind="delete_path_if_created", path=target), ws
    )

    assert (ws / target).exists(), f"откат снёс {target}"
    assert outcome.status == "error", (
        f"снести не удалось, но статус {outcome.status!r} — тихий отказ читается "
        f"как выполненный откат"
    )


def test_work_below_a_protected_root_is_still_removable(tmp_path) -> None:
    """Граница: иначе откат перестал бы делать то, ради чего заведён."""
    ws = _workspace(tmp_path)
    scratch = ws / "data" / "scratch"
    scratch.mkdir()
    (scratch / "созданное.json").write_text("{}", encoding="utf-8")

    outcome = _apply_action(
        CompensationAction(
            kind="delete_path_if_created", path="data/scratch/созданное.json"
        ),
        ws,
    )

    assert outcome.status == "ok"
    assert not (scratch / "созданное.json").exists()
    assert (ws / "data" / "живое.txt").exists(), "задет сосед"


def test_escaping_the_workspace_is_still_refused(tmp_path) -> None:
    """Прежний страж остаётся: новый его дополняет, а не заменяет."""
    ws = _workspace(tmp_path)
    outside = tmp_path.parent / "снаружи.txt"

    outcome = _apply_action(
        CompensationAction(kind="delete_path_if_created", path="../снаружи.txt"), ws
    )

    assert outcome.status == "error"
    assert not outside.exists() or outside.exists()  # существование не наша забота
