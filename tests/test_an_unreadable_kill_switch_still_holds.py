"""Выключатель бюджета: нечитаемое состояние — стоп, сброс без файла ничего не ломает."""
from __future__ import annotations

from pathlib import Path

from core.budget_kill_switch import BudgetKillSwitch


def test_clear_without_a_state_file_is_harmless(tmp_path: Path) -> None:
    """Сброс, когда выключатель не срабатывал, не падает и оставляет его выключенным."""
    ks = BudgetKillSwitch(path=tmp_path / "budget_kill_switch.json")

    ks.clear()

    assert ks.load().active is False
    assert not ks.path.exists()


def test_an_undecodable_state_file_is_a_stop(tmp_path: Path) -> None:
    """Файл состояния, который не читается как UTF-8, держит тормоз."""
    path = tmp_path / "budget_kill_switch.json"
    path.write_bytes(b"\xff\xfe\x00 broken")

    state = BudgetKillSwitch(path=path).load()

    assert state.active is True
    assert state.counter == "state_file_unreadable"


def test_a_state_path_that_cannot_be_read_is_a_stop(tmp_path: Path) -> None:
    """Путь состояния, который нельзя прочитать как файл, держит тормоз, а не снимает его."""
    path = tmp_path / "budget_kill_switch.json"
    path.mkdir()

    state = BudgetKillSwitch(path=path).load()

    assert state.active is True
    assert state.counter == "state_file_unreadable"


def test_clear_that_cannot_remove_the_state_does_not_crash_and_keeps_the_stop(tmp_path: Path) -> None:
    """Сброс, который не смог убрать состояние, не падает, и тормоз остаётся."""
    path = tmp_path / "budget_kill_switch.json"
    path.mkdir()
    ks = BudgetKillSwitch(path=path)

    ks.clear()

    assert ks.load().active is True

