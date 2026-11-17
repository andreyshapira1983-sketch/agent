"""A crash in the agent call or in the local-reply handler does not end the REPL session."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.budget_guard as budget_guard_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
import cli.repl as repl_module
from core.model_usage import ModelBudgetExceeded


def _reader(lines: list[str]):
    pending = list(lines)
    return repl_module._StdinLineReader(
        interactive=False,
        readline=lambda: (pending.pop(0) + "\n") if pending else "",
    )


def _run(lines: list[str], tmp_path, agent=None) -> int:
    limiter = SimpleNamespace(consume=lambda: SimpleNamespace(allowed=True))
    return repl_module.run_repl(
        agent or SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None)),
        reader=_reader(lines), rate_limiter=limiter, workspace=tmp_path,
    )


@pytest.fixture
def quiet_bridge(monkeypatch):
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a: False)
    seen: list[str] = []
    monkeypatch.setattr(
        dispatch_module, "handle_meta_command", lambda cmd, *a: seen.append(cmd) or True
    )
    return seen


@pytest.mark.parametrize("lines", [
    ["what is in the file"],
    [":task-begin", "what is in the file", ":task-end"],
])
def test_an_agent_crash_is_one_line_and_the_session_goes_on(
    tmp_path, monkeypatch, capsys, quiet_bridge, lines
) -> None:
    def boom(agent, **kwargs):
        raise ConnectionError("provider is down")

    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", boom)
    assert _run([*lines, ":mem"], tmp_path) == 0
    assert quiet_bridge == [":mem"]
    err = capsys.readouterr().err
    assert "(command failed: agent run: ConnectionError: provider is down)" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("lines", [
    ["yes"],
    [":task-begin", "yes", ":task-end"],
])
def test_a_local_reply_crash_is_one_line_and_does_not_reach_the_agent(
    tmp_path, monkeypatch, capsys, quiet_bridge, lines
) -> None:
    def boom(text, agent):
        raise KeyError("pending")

    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", boom)
    asked: list = []
    monkeypatch.setattr(repl_module, "_ask_the_agent", lambda *a, **k: asked.append(a))
    assert _run([*lines, ":mem"], tmp_path) == 0
    assert asked == [] and quiet_bridge == [":mem"]
    assert "(command failed: operator reply: KeyError: 'pending')" in capsys.readouterr().err


def test_a_budget_stop_keeps_its_own_message(tmp_path, monkeypatch, capsys, quiet_bridge) -> None:
    def run(**kwargs):
        raise ModelBudgetExceeded("daily cap reached")

    monkeypatch.setattr(budget_guard_module, "_persist_resumable_budget_stop", lambda *a, **k: None)
    monkeypatch.setattr(repl_module, "_judged_human_reply", lambda agent, q, answer: answer)
    agent = SimpleNamespace(run=run, log=SimpleNamespace(log=lambda *a, **k: None))
    assert _run(["do the work"], tmp_path, agent) == 0
    out, err = capsys.readouterr()
    assert "Model budget exceeded: daily cap reached" in out
    assert "command failed" not in err


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_interrupt_and_exit_in_the_agent_call_keep_their_meaning(
    tmp_path, monkeypatch, quiet_bridge, exc_type
) -> None:
    def stop(agent, **kwargs):
        raise exc_type()

    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", stop)
    with pytest.raises(exc_type):
        _run(["do the work"], tmp_path)
