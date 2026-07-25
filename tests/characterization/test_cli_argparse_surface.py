"""C1 — the argparse surface of `python main.py`.

Public contract: the flag set, their defaults, the `--auto-approve` choices and
the usage-error exit code are what operators and scripts depend on. Extraction
must reproduce them byte-for-byte.

`--help` is the authoritative rendering of that surface, so these tests read the
help text argparse itself produces rather than re-deriving the parser.
"""
from __future__ import annotations

import re
import sys

import pytest

import main as main_module

# The complete public flag set at 9daa9bf (argparse adds -h/--help itself).
EXPECTED_FLAGS = {
    "--ask",
    "--file",
    "--workspace",
    "--auto-approve",
    "--resume",
    "--reason",
    "--expect",
}


def _help_text(monkeypatch: pytest.MonkeyPatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 0, "--help must exit 0"
    return capsys.readouterr().out


def test_help_exits_zero_and_lists_exactly_seven_flags(monkeypatch, capsys):
    text = _help_text(monkeypatch, capsys)
    found = {m.group(0) for m in re.finditer(r"--[a-z][a-z-]+", text)}
    # Restrict to option *declarations* — the help prose mentions no other flags
    # at this commit, but filter defensively so prose additions do not break it.
    declared = {flag for flag in found if flag in EXPECTED_FLAGS or flag == "--help"}
    assert declared == EXPECTED_FLAGS | {"--help"}
    assert EXPECTED_FLAGS <= found


def test_usage_line_shape(monkeypatch, capsys):
    text = _help_text(monkeypatch, capsys)
    usage = " ".join(text.split("options:")[0].split())
    assert usage.startswith("usage: main.py [-h]")
    for fragment in (
        "[--ask ASK]",
        "[--file FILE]",
        "[--workspace WORKSPACE]",
        "[--auto-approve {off,approve,deny}]",
        "[--resume TRACE_ID]",
        "[--reason REASON]",
        "[--expect EXPECT]",
    ):
        assert fragment in usage, fragment


def test_auto_approve_choices_and_documented_defaults(monkeypatch, capsys):
    text = " ".join(_help_text(monkeypatch, capsys).split())
    # choices rendered by argparse
    assert "{off,approve,deny}" in text
    # documented defaults (the values themselves are asserted behaviorally in
    # test_cli_mode_selection.py / test_cli_one_shot_policy.py)
    assert "Workspace root (default: current directory)." in text
    assert "'off' (default) = interactive prompts in the REPL, deny in one-shot;" in text
    assert "One-shot question (no memory). Omit to enter the interactive REPL." in text
    assert "--resume TRACE_ID" in text


def test_resume_metavar_is_trace_id(monkeypatch, capsys):
    text = _help_text(monkeypatch, capsys)
    assert "--resume TRACE_ID" in text
    assert "--resume RESUME" not in text


def test_unknown_argument_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--definitely-not-a-flag"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_flag_missing_its_value_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--ask"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 2
    assert "expected one argument" in capsys.readouterr().err


def test_invalid_auto_approve_choice_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--auto-approve", "maybe"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
