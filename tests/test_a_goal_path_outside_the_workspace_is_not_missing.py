"""A path the goal names outside the workspace is looked up on disk, not declared missing.

Live run 26.09: the operator's goal named /root/models/whisper-large-v3-turbo and
/root/ears/.venv; the token lost its «/», was looked up inside /root/agent-main,
and three cycles answered «the goal names … which does not exist in the workspace,
so it binds no work» — the run stopped as healthy_idle after 4 minutes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.best_next_action import unresolved_goal_targets

GOAL = "Подключи уши: /root/models/whisper-large-v3-turbo, окружение /root/ears/.venv, образец core/ears_probe.py"


def test_an_absolute_path_that_exists_on_disk_is_not_missing() -> None:
    on_disk = {"/root/models/whisper-large-v3-turbo", "/root/ears/.venv"}
    missing = unresolved_goal_targets(GOAL, exists=lambda _rel: False, exists_abs=on_disk.__contains__)
    assert missing == ("core/ears_probe.py",), missing


def test_an_absolute_path_that_is_nowhere_is_still_missing() -> None:
    missing = unresolved_goal_targets(GOAL, exists=lambda _rel: False, exists_abs=lambda _p: False)
    assert "root/models/whisper-large-v3-turbo" in missing


@pytest.mark.skipif(os.name == "nt", reason="absolute POSIX path in the goal text")
def test_a_real_folder_outside_the_workspace_is_found(tmp_path: Path) -> None:
    models = tmp_path / "models" / "whisper"
    models.mkdir(parents=True)
    goal = f"Подключи модель {models} как инструмент"
    assert unresolved_goal_targets(goal, exists=lambda _rel: False) == ()
