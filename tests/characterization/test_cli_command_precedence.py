"""C7 — explicit `:command` precedence over fuzzy operator-intent routing.

Public contract in both modes: a message whose first token starts with `:` (or is
exactly `?`) is dispatched as an explicit command and is never handed to the
keyword/intent router. Without this, e.g. `:campaign-start --max-cost-units 0`
would be misread as a budget query.

Both collaborators are replaced with recorders, so the assertion is purely about
*which* path the CLI chooses — no intent pattern, model veto or handler runs.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import cli.repl as repl_module
import cli.app as app_module
import app.budget_guard as budget_guard_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
import main as main_module


_REAL_STDIN_READER = repl_module._StdinLineReader


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _scripted_reader(lines: list[str]):
    pending = list(lines)

    def readline() -> str:
        if pending:
            return pending.pop(0) + "\n"
        return ""

    return _REAL_STDIN_READER(interactive=False, readline=readline)


def _patch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which router the CLI reaches, in order."""
    calls: list[str] = []
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)

    def fake_meta(cmd, agent, workspace):
        calls.append(f"meta:{cmd}")
        return True

    def fake_local(text, agent):
        calls.append(f"local:{text}")
        return False

    def fake_conversational(text, agent, workspace):
        calls.append(f"conversational:{text}")
        return True  # claim it, so no agent run is needed

    monkeypatch.setattr(dispatch_module, "handle_meta_command", fake_meta)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", fake_local)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", fake_conversational)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard",
        lambda *a, **k: pytest.fail("no agent run expected on these paths"),
    )
    return calls


# ── one-shot ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ask",
    [
        ":models",
        ":operator-check",
        ":campaign-start --max-cost-units 0",
        "?",
        "  :models",  # leading whitespace is stripped before the ':' test
    ],
)
def test_one_shot_explicit_command_beats_intent_routing(ask, tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ask])

    assert main_module.main() == 0

    assert len(calls) == 1
    assert calls[0] == f"meta:{ask.lstrip()}"
    assert not any(c.startswith("conversational:") for c in calls)
    assert not any(c.startswith("local:") for c in calls)


def test_one_shot_plain_text_reaches_the_intent_router(tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "check the project health"]
    )

    assert main_module.main() == 0

    assert calls == [
        "local:check the project health",
        "conversational:check the project health",
    ]


def test_one_shot_local_reply_short_circuits_before_the_intent_router(tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda text, agent: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "anything"])

    assert main_module.main() == 0
    assert calls == []


# ── REPL ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [":models", "?", "  :models  "])
def test_repl_explicit_command_beats_intent_routing(line, tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([line]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    assert calls == [f"meta:{line.strip()}"]


def test_repl_plain_text_reaches_the_intent_router(tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader(["what models are used"])
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    assert calls == [
        "local:what models are used",
        "conversational:what models are used",
    ]


def test_repl_unknown_command_reports_and_keeps_the_loop_alive(tmp_path, monkeypatch, capsys):
    calls = _patch(monkeypatch)
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: False)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([":nope", ":also-nope"])
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    err = capsys.readouterr().err
    assert "(unknown command: :nope)" in err
    assert "(unknown command: :also-nope)" in err
    assert calls == []


def test_repl_quit_propagates_system_exit_zero(tmp_path, monkeypatch):
    """`:quit` raises SystemExit from inside the dispatcher rather than
    returning a value; extraction must keep that observable behavior."""
    _patch(monkeypatch)
    monkeypatch.setattr(dispatch_module, "handle_meta_command", dispatch_module.handle_meta_command)

    def quitting_meta(cmd, agent, workspace):
        raise SystemExit(0)

    monkeypatch.setattr(dispatch_module, "handle_meta_command", quitting_meta)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([":quit"]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 0


def test_real_dispatcher_raises_system_exit_for_quit_and_exit(tmp_path):
    for cmd in (":quit", ":exit"):
        with pytest.raises(SystemExit) as excinfo:
            dispatch_module.handle_meta_command(cmd, SimpleNamespace(), tmp_path)
        assert excinfo.value.code == 0, cmd
