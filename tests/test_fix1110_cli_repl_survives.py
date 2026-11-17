"""A crashing command handler must not end the REPL session."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.operator_task as operator_task_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
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


def test_meta_command_crash_is_reported_and_the_loop_continues(tmp_path, monkeypatch, capsys):
    seen: list[str] = []

    def handler(cmd, agent, workspace):
        seen.append(cmd)
        if cmd.startswith(":hygiene"):
            raise ValueError("invalid literal for int(): 'abc'")
        return True

    monkeypatch.setattr(dispatch_module, "handle_meta_command", handler)
    events: list = []
    assert _run([":hygiene archive --threshold=abc", ":mem"], tmp_path, events) == 0
    assert seen == [":hygiene archive --threshold=abc", ":mem"]
    err = capsys.readouterr().err
    assert "(command failed: :hygiene: ValueError: invalid literal" in err
    assert "unknown command" not in err
    assert events and events[0][0] == "repl_command_error"


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


def test_conversational_handler_crash_does_not_reach_the_agent(tmp_path, monkeypatch, capsys):
    def boom(q, agent, workspace):
        raise KeyError("missing")

    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", boom)
    asked: list = []
    monkeypatch.setattr(repl_module, "_ask_the_agent", lambda *a, **k: asked.append(a))
    assert _run(["check the project"], tmp_path) == 0
    assert asked == []
    assert "KeyError" in capsys.readouterr().err


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


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_interrupt_and_exit_keep_their_meaning(tmp_path, monkeypatch, exc_type):
    def stop(cmd, agent, workspace):
        raise exc_type()

    monkeypatch.setattr(dispatch_module, "handle_meta_command", stop)
    with pytest.raises(exc_type):
        _run([":quit"], tmp_path)
