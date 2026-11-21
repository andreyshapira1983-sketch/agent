"""A crashing command handler must not end the REPL session."""
from __future__ import annotations

from types import SimpleNamespace

import app.operator_task as operator_task_module
import cli.command_dispatch as dispatch_module
import cli.repl as repl_module


def _reader(lines: list[str]):
    pending = list(lines)
    return repl_module._StdinLineReader(
        interactive=False,
        readline=lambda: (pending.pop(0) + "\n") if pending else "",
    )


def _agent(events: list) -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: events.append(a)))


def _run(lines: list[str], tmp_path, events: list | None = None) -> int:
    return repl_module.run_repl(
        _agent(events if events is not None else []),
        reader=_reader(lines),
        rate_limiter=SimpleNamespace(),
        workspace=tmp_path,
    )


def test_operator_task_block_crash_does_not_end_the_session(tmp_path, monkeypatch, capsys):
    def boom(block, agent, workspace):
        raise RuntimeError("not a git repository")

    seen: list[str] = []
    monkeypatch.setattr(operator_task_module, "_handle_operator_task", boom)
    monkeypatch.setattr(
        dispatch_module, "handle_meta_command", lambda cmd, *a: seen.append(cmd) or True
    )
    assert _run([":operator-task", "body", ":end", ":mem"], tmp_path) == 0
    assert seen == [":mem"]
    assert "(command failed: :operator-task: RuntimeError: not a git repository)" in capsys.readouterr().err


def test_a_failing_log_does_not_break_the_report(tmp_path, monkeypatch, capsys):
    def boom(cmd, agent, workspace):
        raise ValueError("bad")

    def bad_log(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dispatch_module, "handle_meta_command", boom)
    agent = SimpleNamespace(log=SimpleNamespace(log=bad_log))
    rc = repl_module.run_repl(
        agent, reader=_reader([":x"]), rate_limiter=SimpleNamespace(), workspace=tmp_path
    )
    assert rc == 0
    assert "ValueError: bad" in capsys.readouterr().err
