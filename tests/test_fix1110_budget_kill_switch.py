"""A kill-switch that could not be cleared says so."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.commands_budget import _handle_budget_kill_switch
from core.budget_kill_switch import BudgetKillSwitch, KillSwitchState, default_path


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


def _latched(path: Path) -> BudgetKillSwitch:
    ks = BudgetKillSwitch(path=path)
    ks._write(KillSwitchState(active=True, reason="budget_kill_switch", counter="llm_calls"))
    return ks


def _lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(self, *args, **kwargs):
        raise PermissionError(13, "file is used by another process")

    monkeypatch.setattr(Path, "unlink", locked)


def test_clear_raises_when_the_file_cannot_be_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ks = _latched(tmp_path / "ks.json")
    _lock(monkeypatch)

    with pytest.raises(PermissionError):
        ks.clear()
    assert ks.load().active


def test_clear_is_still_a_noop_when_absent(tmp_path: Path) -> None:
    BudgetKillSwitch(path=tmp_path / "ks.json").clear()


def _agent() -> SimpleNamespace:
    return SimpleNamespace(log=_Log(), model_router=SimpleNamespace(usage_ledger=None))


def test_command_does_not_log_cleared_when_clear_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _latched(default_path(tmp_path))
    agent = _agent()
    _lock(monkeypatch)

    assert _handle_budget_kill_switch("--clear", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "budget_kill_switch_cleared" not in agent.log.kinds()
    assert "budget_kill_switch_clear_failed" in agent.log.kinds()
    assert "NOT cleared" in err and "PermissionError" in err
    assert "active: True" in err


def test_command_logs_cleared_when_clear_worked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _latched(default_path(tmp_path))
    agent = _agent()

    _handle_budget_kill_switch("--clear", agent, tmp_path)

    assert "budget_kill_switch_cleared" in agent.log.kinds()
    assert not default_path(tmp_path).exists()
    assert "NOT cleared" not in capsys.readouterr().err
