"""A bad number, a missing git repository and a crashing one-shot command end in one line."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli.command_dispatch as dispatch_module
import cli.commands_self_build as self_build_module
from cli.command_dispatch import handle_meta_command
from cli.one_shot import run_one_shot


class _Report(SimpleNamespace):
    scanned = 7
    archived = ()
    threshold = 0.2
    min_age_days = 30


def _hygiene_agent() -> tuple[SimpleNamespace, list]:
    calls: list = []

    def archive_persistent(**kwargs):
        calls.append(kwargs)
        return _Report()

    return SimpleNamespace(archive_persistent=archive_persistent), calls


@pytest.mark.parametrize("args", [
    "--threshold=abc", "--threshold=", "--min-age=soon", "--min-age=1.5", "--threshold=nan",
    "--threshold=0.2 --min-age=x",
])
def test_hygiene_archive_with_a_bad_number_prints_usage(tmp_path, capsys, args) -> None:
    agent, calls = _hygiene_agent()
    assert handle_meta_command(f":hygiene archive {args}", agent, tmp_path) is True
    assert calls == []
    err = capsys.readouterr().err.strip()
    assert err == "Usage: :hygiene archive [--threshold=<number>] [--min-age=<days>] [--dry-run]"


def test_hygiene_archive_with_good_numbers_is_unchanged(tmp_path, capsys) -> None:
    agent, calls = _hygiene_agent()
    handle_meta_command(":hygiene archive --threshold=0.2 --min-age=30 --dry-run", agent, tmp_path)
    assert calls == [{"threshold": 0.2, "min_age_days": 30, "dry_run": True}]
    assert "archive (dry_run=True): scanned 7 record(s)" in capsys.readouterr().err


def test_self_build_produce_outside_a_git_repository_refuses(tmp_path, capsys, monkeypatch) -> None:
    """The real producer and the real SafeVCS: `git status` fails in a plain directory."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(self_build_module, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        self_build_module, "BudgetKillSwitch",
        lambda path: SimpleNamespace(status=lambda snap: SimpleNamespace(active=False, engaged=False)),
    )
    events: list = []
    agent = SimpleNamespace(
        log=SimpleNamespace(log=lambda *a, **k: events.append(a)),
        model_router=SimpleNamespace(for_role=lambda role: None),
    )
    assert handle_meta_command(":self-build-produce", agent, tmp_path) is True
    err = capsys.readouterr().err.strip()
    assert err.startswith("(:self-build-produce refused — the workspace is not a usable git repository:")
    assert "\n" not in err


def _one_shot(ask: str, tmp_path, monkeypatch, handler) -> int:
    monkeypatch.setattr(dispatch_module, "handle_meta_command", handler)
    return run_one_shot(ask, workspace=tmp_path, build_agent=lambda *a, **k: SimpleNamespace(memory=None))


def test_one_shot_reports_a_crashing_command_and_fails(tmp_path, capsys, monkeypatch) -> None:
    def boom(cmd, agent, workspace):
        raise RuntimeError("store is locked")

    assert _one_shot(":mem --json", tmp_path, monkeypatch, boom) == 1
    assert capsys.readouterr().err.strip() == "(command failed: :mem: RuntimeError: store is locked)"


def test_one_shot_keeps_zero_for_handled_and_unknown_commands(tmp_path, capsys, monkeypatch) -> None:
    assert _one_shot(":mem", tmp_path, monkeypatch, lambda *a: True) == 0
    assert _one_shot(":nope", tmp_path, monkeypatch, lambda *a: False) == 0
    assert "(unknown command: :nope)" in capsys.readouterr().err


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_one_shot_interrupt_and_exit_keep_their_meaning(tmp_path, monkeypatch, exc_type) -> None:
    def stop(cmd, agent, workspace):
        raise exc_type()

    with pytest.raises(exc_type):
        _one_shot(":quit", tmp_path, monkeypatch, stop)
