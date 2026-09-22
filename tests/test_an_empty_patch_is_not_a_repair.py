"""Цель самопочинки судит patch_check, а не наличие файла.

Замер 2026-09-23, первая живая цель после того, как записи разблокировали:
агент создал `proposals/selffix/sii_5dab4ac83cc87892/edits.txt` — каркас из
двух заголовков блоков без единой строки замены. Судья цели сказал
«verified», потому что критерий требовал ровно «файл создан»; `patch_check`
в ту же минуту записал в журнал самопочинки «red: text outside blocks».

Два прибора о той же работе, и наружу шёл тот, который меряет не то, — тот же
класс, что метка «урок» за пустой ход и «сделано» без проверки. Настоящая
проверка у этой цели уже была; вердикт просто спрашивал не её.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.campaign_verdict import judge_campaign
from core.patch_route import patch_goal_verdict

_CHECK = "Файл proposals/selffix/sii_demo/edits.txt создан"
_REL = "proposals/selffix/sii_demo/edits.txt"


def _workspace(tmp_path: Path, patch_text: str | None) -> Path:
    target = tmp_path / "core" / "sample.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    # patch_check примеряет правку на КЛОНЕ рабочей копии, поэтому ей нужен
    # настоящий репозиторий — иначе инструмент не отрабатывает и честно
    # отвечает «непроверяемо» вместо «сделано».
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=a@b", "-c", "user.name=t",
                 "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=tmp_path, check=True)  # noqa: S603
    if patch_text is not None:
        path = tmp_path / _REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(patch_text, encoding="utf-8")
    return tmp_path


def test_an_empty_frame_of_blocks_is_not_a_repair(tmp_path: Path) -> None:
    """Дословно тот файл, что агент написал 2026-09-23 в 22:53."""
    ws = _workspace(tmp_path, "FILE:core/sample.py\n<<<<<<< LINES 1-2\n=======\n>>>>>>> REPLACE\n")

    verdict = judge_campaign(goal="Почини свой дефект", success_check=_CHECK, workspace=ws)

    assert verdict["verdict"] != "verified", verdict
    assert "patch_check" in verdict["reason"]


def test_a_missing_patch_file_is_named_missing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, None)

    verdict = judge_campaign(goal="Почини свой дефект", success_check=_CHECK, workspace=ws)

    assert verdict["verdict"] == "missing"
    assert _REL in verdict["reason"]


def test_a_real_patch_passes(tmp_path: Path) -> None:
    """Граница: настоящая правка по-прежнему засчитывается."""
    # Правка без своего теста не зелёная — правило самого patch_check,
    # поэтому образец настоящей правки несёт и замену, и новый тест.
    ws = _workspace(tmp_path, (
        "FILE:core/sample.py\n<<<<<<< SEARCH\n    return 'hello'\n"
        "=======\n    return 'hi'\n>>>>>>> REPLACE\n"
        "FILE:tests/test_greet_says_hi.py\n<<<<<<< SEARCH\n=======\n"
        "from core.sample import greet\n\n\n"
        "def test_greet_says_hi() -> None:\n    assert greet() == 'hi'\n"
        ">>>>>>> REPLACE\n"))

    verdict = patch_goal_verdict(ws, _CHECK)

    assert verdict is not None
    assert verdict["verdict"] == "verified", verdict


def test_an_ordinary_goal_is_left_to_the_file_judge(tmp_path: Path) -> None:
    """Цель не про правку судит прежний наблюдатель мира."""
    assert patch_goal_verdict(tmp_path, "Файл data/notes/x.md создан") is None
