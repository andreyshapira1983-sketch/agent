"""C5 — stdout versus stderr, and the exact answer wrapper.

Recorded contract at 9daa9bf: **stdout carries answers only**; every operator
notice, prompt, banner, help page, rate-limit warning and unknown-command
message goes to stderr. Scripts that pipe stdout therefore receive answers
alone, which is why this split is a public contract rather than an incidental
detail.

The answer wrapper is `print("\\n" + format_human_response(answer) + "\\n")`, so
captured stdout is `"\\n" + rendered + "\\n\\n"` (print supplies the last newline).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.repl as repl_module
import cli.app as app_module
import app.budget_guard as budget_guard_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
import main as main_module
from core.checkpoint import CheckpointWriter
from core.loop import format_human_response


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


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", lambda *a, **k: _fake_agent())
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)


def test_one_shot_answer_goes_to_stdout_with_the_blank_line_wrapper(
    tmp_path, monkeypatch, capsys
):
    _patch(monkeypatch)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "ANSWER-BODY")
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert out.out == "\n" + format_human_response("ANSWER-BODY") + "\n\n"
    assert "ANSWER-BODY" not in out.err


def test_cached_resume_answer_uses_the_same_wrapper_on_stdout(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    writer = CheckpointWriter("run_stream", tmp_path / "logs")
    writer.save_observe("q", file_hint=None)
    writer.save_respond("REPLAYED-BODY")
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--resume", "run_stream"]
    )

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert out.out == "\n" + format_human_response("REPLAYED-BODY") + "\n\n"
    assert "[resume] Replaying cached answer" in out.err


def test_notices_and_errors_are_stderr_only(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--file", "nope.md", "--ask", "q"],
    )

    assert main_module.main() == 2

    out = capsys.readouterr()
    assert out.out == ""
    assert "ERROR: file hint does not exist:" in out.err


def test_unknown_one_shot_command_message_is_stderr_and_returns_zero(
    tmp_path, monkeypatch, capsys
):
    _patch(monkeypatch)
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: False)
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":no-such-command"]
    )

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert out.out == ""
    assert "(unknown command: :no-such-command)" in out.err


def test_help_page_is_written_to_stderr(tmp_path, capsys):
    """`:help` needs no agent state — it only prints."""
    assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), tmp_path) is True

    out = capsys.readouterr()
    assert out.out == ""
    assert "Commands:" in out.err
    assert ":quit | :exit" in out.err
    assert "Conversational shortcuts:" in out.err


def test_repl_banner_is_stderr_and_repl_prompt_is_stdout(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert "Agent ready." in out.err
    # The reader writes its prompt to sys.stdout (its default `out`), and EOF
    # prints one bare newline to stdout before returning 0.
    assert out.out == "> \n"


def test_block_mode_prompt_also_goes_to_stdout(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "A")
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader(["<<<", "body", ">>>"])
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert "> " in out.out
    assert "... " in out.out
    assert "(multi-line mode: paste text, finish with >>> on its own line)" in out.err


def test_approval_prompt_is_written_to_stderr_and_reads_the_repl_reader(
    tmp_path, monkeypatch, capsys
):
    """The interactive approval provider shares the single stdin reader."""
    _patch(monkeypatch)
    captured: dict = {}

    class _CapturingProvider:
        def __init__(self, input_fn):
            captured["input_fn"] = input_fn

    monkeypatch.setattr(app_module, "CLIApprovalProvider", _CapturingProvider)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0
    capsys.readouterr()  # discard the banner/prompt from the run itself

    input_fn = captured["input_fn"]
    # The reader is already at EOF, so the read raises after the prompt is shown.
    with pytest.raises(EOFError):
        input_fn("Approve this action? [y/N] ")

    out = capsys.readouterr()
    assert out.err == "Approve this action? [y/N] "
    assert out.out == ""
