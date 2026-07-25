"""C9 — which REPL paths consume the session rate limiter, and which bypass it.

**Observed extraction baseline, not an endorsed final policy.** At 9daa9bf the
limiter is consumed only on the two paths that reach the agent loop; explicit
`:commands`, local operator replies and intent-routed messages are never
counted, even though the last of those can run an expensive handler. Extraction
must preserve this asymmetry so a refactor is not silently a policy change;
correcting the policy is a separate, explicit decision (see
docs/refactor/CLI_BASELINE.md §3).

`main()` constructs the limiter itself (`from core.rate_limiter import
CLIRateLimiter` inside the function), so the class is patched at its source
module — that import runs at call time.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import core.rate_limiter as rate_limiter_module
import app.budget_guard as budget_guard_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
import main as main_module


_REAL_STDIN_READER = main_module._StdinLineReader


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _scripted_reader(lines: list[str]):
    pending = list(lines)

    def readline() -> str:
        if pending:
            return pending.pop(0) + "\n"
        return ""

    return _REAL_STDIN_READER(interactive=False, readline=readline)


def _install_limiter(monkeypatch: pytest.MonkeyPatch, *, allowed: bool = True) -> dict:
    state: dict = {"init": [], "consumed": 0}

    class _RecordingLimiter:
        def __init__(self, max_requests, window_seconds):
            state["init"].append((max_requests, window_seconds))

        def consume(self):
            state["consumed"] += 1
            return SimpleNamespace(
                allowed=allowed, retry_after_seconds=1.5, tokens_remaining=0.25
            )

    monkeypatch.setattr(rate_limiter_module, "CLIRateLimiter", _RecordingLimiter)
    return state


def _run_repl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    lines: list[str],
    *,
    local_reply: bool = False,
    conversational: bool = False,
    meta: bool = True,
) -> list[dict]:
    runs: list[dict] = []
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(main_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: meta)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: local_reply)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: conversational
    )
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda agent, **kw: runs.append(kw) or "A"
    )
    monkeypatch.setattr(main_module, "_StdinLineReader", lambda **k: _scripted_reader(lines))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])
    assert main_module.main() == 0
    return runs


def test_limiter_is_constructed_with_thirty_per_sixty_seconds(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    _run_repl(monkeypatch, tmp_path, [])
    assert state["init"] == [(30, 60.0)]


def test_plain_question_consumes_one_token(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    runs = _run_repl(monkeypatch, tmp_path, ["a plain question"])
    assert len(runs) == 1
    assert state["consumed"] == 1


def test_instruction_buffer_consumes_one_token(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    runs = _run_repl(monkeypatch, tmp_path, [":task-begin", "buffered work", ":task-end"])
    assert len(runs) == 1
    assert state["consumed"] == 1


def test_explicit_command_bypasses_the_limiter(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    runs = _run_repl(monkeypatch, tmp_path, [":models", ":models", ":models"])
    assert runs == []
    assert state["consumed"] == 0


def test_local_operator_reply_bypasses_the_limiter(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    runs = _run_repl(monkeypatch, tmp_path, ["stop now"], local_reply=True)
    assert runs == []
    assert state["consumed"] == 0


def test_intent_routed_message_bypasses_the_limiter(tmp_path, monkeypatch):
    """Recorded gap: an intent-routed handler runs without consuming a token."""
    state = _install_limiter(monkeypatch)
    runs = _run_repl(monkeypatch, tmp_path, ["check the project health"], conversational=True)
    assert runs == []
    assert state["consumed"] == 0


def test_instruction_buffer_checks_local_reply_before_the_limiter(tmp_path, monkeypatch):
    state = _install_limiter(monkeypatch)
    runs = _run_repl(
        monkeypatch, tmp_path, [":task-begin", "body", ":task-end"], local_reply=True
    )
    assert runs == []
    assert state["consumed"] == 0


def test_blocked_limiter_reports_to_stderr_and_skips_the_agent(tmp_path, monkeypatch, capsys):
    state = _install_limiter(monkeypatch, allowed=False)
    runs = _run_repl(monkeypatch, tmp_path, ["a plain question"])
    assert runs == []
    assert state["consumed"] == 1
    err = capsys.readouterr().err
    assert "(rate limit: too many requests — retry in 1.5s, tokens remaining: 0.25)" in err


def test_blocked_limiter_on_the_instruction_buffer_path(tmp_path, monkeypatch, capsys):
    state = _install_limiter(monkeypatch, allowed=False)
    runs = _run_repl(monkeypatch, tmp_path, [":task-begin", "body", ":task-end"])
    assert runs == []
    assert state["consumed"] == 1
    assert "(rate limit: too many requests" in capsys.readouterr().err


def test_blocked_limiter_keeps_the_repl_alive(tmp_path, monkeypatch):
    """A rate-limited message must not exit the REPL."""
    state = _install_limiter(monkeypatch, allowed=False)
    runs = _run_repl(monkeypatch, tmp_path, ["one", "two", "three"])
    assert runs == []
    assert state["consumed"] == 3


def test_one_shot_mode_has_no_rate_limiter(tmp_path, monkeypatch):
    """The limiter is a REPL-session control; one-shot never constructs it."""
    state = _install_limiter(monkeypatch)
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "A")
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])

    assert main_module.main() == 0
    assert state["init"] == []
    assert state["consumed"] == 0
