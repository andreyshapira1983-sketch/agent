"""C8 — REPL input modes and stdin ownership.

Public contract: a pasted or multi-line instruction must arrive as ONE message,
an empty Enter must never exit the REPL, and the two block modes end on their
documented terminators (`>>>`, `:task-end`/`:task-abort`, `:end`).

The reader is driven through the real `_StdinLineReader` with an injected
`readline`, the same technique tests/test_paste_coalesce.py uses, so the class
under test is production code rather than a stub. `interactive=False` keeps the
line stream deterministic; burst coalescing itself is covered by
tests/test_paste_coalesce.py.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import cli.app as app_module
import app.budget_guard as budget_guard_module
import app.operator_task as operator_task_module
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


def _run_repl(monkeypatch: pytest.MonkeyPatch, tmp_path, lines: list[str]) -> list[dict]:
    """Run the REPL over `lines`; return the recorded agent-run kwargs."""
    runs: list[dict] = []
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)

    def fake_run(agent, **kwargs):
        runs.append(kwargs)
        return "ANSWER"

    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", fake_run)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader(lines))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])
    assert main_module.main() == 0
    return runs


# ── mode 1: <<< ... >>> ───────────────────────────────────────────────────────

def test_block_mode_joins_lines_with_newlines_into_one_message(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, ["<<<", "line one", "line two", ">>>"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "line one\nline two"


def test_block_mode_accepts_a_glued_terminator(tmp_path, monkeypatch):
    """A paste whose buffer ends `...text>>>` without a newline must still end."""
    runs = _run_repl(monkeypatch, tmp_path, ["<<<", "line one", "line two.>>>"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "line one\nline two."


def test_empty_block_is_discarded_without_running_the_agent(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, ["<<<", "   ", ">>>"])
    assert runs == []


def test_block_mode_eof_exits_the_repl(tmp_path, monkeypatch, capsys):
    runs = _run_repl(monkeypatch, tmp_path, ["<<<", "unterminated"])
    assert runs == []


# ── mode 2: trailing backslash continuation ───────────────────────────────────

def test_backslash_continuation_joins_with_single_spaces(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, ["first \\", "second"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "first second"


def test_multiple_continuation_lines_are_joined(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, ["a \\", "b \\", "c"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "a b c"


# ── empty input ───────────────────────────────────────────────────────────────

def test_empty_line_does_not_exit_and_does_not_run_the_agent(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, ["", "   ", "real question"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "real question"


# ── instruction buffer: :task-begin ... :task-end / :task-abort ───────────────

def test_instruction_buffer_commits_on_task_end(tmp_path, monkeypatch, capsys):
    runs = _run_repl(
        monkeypatch, tmp_path, [":task-begin", "do the thing", "carefully", ":task-end"]
    )
    assert len(runs) == 1
    assert runs[0]["user_question"] == "do the thing\ncarefully"
    err = capsys.readouterr().err
    assert "(instruction buffer started; finish with :task-end, discard with :task-abort)" in err


def test_instruction_buffer_discards_on_task_abort(tmp_path, monkeypatch, capsys):
    runs = _run_repl(monkeypatch, tmp_path, [":task-begin", "throw this away", ":task-abort"])
    assert runs == []
    assert "(instruction buffer cancelled)" in capsys.readouterr().err


def test_empty_instruction_buffer_sends_nothing(tmp_path, monkeypatch, capsys):
    runs = _run_repl(monkeypatch, tmp_path, [":task-begin", "   ", ":task-end"])
    assert runs == []
    assert "(instruction buffer empty — nothing sent)" in capsys.readouterr().err


def test_instruction_buffer_bypasses_the_keyword_router(tmp_path, monkeypatch):
    """`:task-begin` text goes straight to the agent — the intent router that
    would hijack wording mentioning budget/approval is not consulted."""
    runs: list[dict] = []
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input",
        lambda *a, **k: pytest.fail("instruction buffer must bypass the intent router"),
    )
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda agent, **kw: runs.append(kw) or "A"
    )
    monkeypatch.setattr(app_module, "_StdinLineReader",
        lambda **k: _scripted_reader([":task-begin", "check the budget status", ":task-end"]),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0
    assert runs[0]["user_question"] == "check the budget status"


def test_task_end_and_task_abort_are_case_insensitive(tmp_path, monkeypatch):
    runs = _run_repl(monkeypatch, tmp_path, [":task-begin", "body", ":TASK-END"])
    assert len(runs) == 1
    assert runs[0]["user_question"] == "body"


# ── :operator-task ... :end ───────────────────────────────────────────────────

def test_operator_task_block_ends_on_end_and_calls_its_handler(tmp_path, monkeypatch, capsys):
    seen: list[str] = []
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(operator_task_module, "_handle_operator_task",
        lambda text, agent, workspace: seen.append(text) or True,
    )
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard",
        lambda *a, **k: pytest.fail("operator-task block must not run the agent loop"),
    )
    monkeypatch.setattr(app_module, "_StdinLineReader",
        lambda **k: _scripted_reader([":operator-task", "step one", "step two", ":end"]),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    assert seen == ["step one\nstep two"]
    assert "(operator task block started; finish with :end)" in capsys.readouterr().err


# ── stdin ownership ───────────────────────────────────────────────────────────

def test_repl_creates_exactly_one_reader(tmp_path, monkeypatch):
    """One reader owns stdin for the prompt, block modes and approval prompt."""
    built: list[dict] = []

    def factory(**kwargs):
        built.append(kwargs)
        return _scripted_reader(["<<<", "x", ">>>", ":task-begin", "y", ":task-end"])

    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "A")
    monkeypatch.setattr(app_module, "_StdinLineReader", factory)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0
    assert len(built) == 1


def test_reader_prompts_go_to_stdout_and_eof_raises(capsys):
    reader = _scripted_reader(["only line"])
    assert reader.prompt_line("... ") == "only line"
    with pytest.raises(EOFError):
        reader.read_line()
    assert capsys.readouterr().out == "... "


def test_paste_gap_constant_is_frozen():
    assert main_module.PASTE_COALESCE_GAP_SECONDS == 0.05
