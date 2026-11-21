"""C2/C10 — which mode `main()` selects, and how each mode is constructed.

`--ask` selects one-shot; its absence selects the interactive REPL. This module
also records the REPL's startup sequence (daemon notice before the banner) and
the memory/approval policy each mode builds with.

Every run monkeypatches `main.load_dotenv` and `main.build_agent`: reading the
operator's real `.env` or constructing a provider-backed agent is not the
behavior under characterization here.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import cli.app as app_module
import main as main_module


def _fake_agent() -> SimpleNamespace:
    """Minimal stand-in: only `.log.log` is reached on these paths."""
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _patch_common(monkeypatch: pytest.MonkeyPatch, build_calls: list[dict]) -> None:
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)

    def fake_build_agent(workspace, **kwargs):
        build_calls.append({"workspace": workspace, **kwargs})
        return _fake_agent()

    monkeypatch.setattr(app_module, "build_agent", fake_build_agent)


def test_missing_file_hint_exits_two_before_building_agent(tmp_path, monkeypatch, capsys):
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--file", "missing.md", "--ask", "q"],
    )

    assert main_module.main() == 2

    assert build_calls == []
    err = capsys.readouterr().err
    assert "ERROR: file hint does not exist:" in err
    assert "No model calls were made." in err


def test_empty_ask_exits_two_instead_of_silently_opening_the_repl(tmp_path, monkeypatch, capsys):
    """`--ask ""` asked for a one-shot answer and got an interactive session.

    The same truthiness trap the `--resume` comment in cli/app.py describes at
    length, left standing twelve lines below it: `if args.ask:` is false for an
    empty string, so the branch was skipped and the REPL opened. `--resume ""`
    exits 2 and says why; `--ask ""` did neither.

    Measured 2026-08-05 before the fix: exit 0, interactive mode, no message.
    """
    monkeypatch.setattr(
        app_module, "run_one_shot",
        lambda *a, **k: pytest.fail("an empty question must not reach one-shot"),
    )
    monkeypatch.setattr(
        app_module, "build_agent",
        lambda *a, **k: pytest.fail("an empty question must not build an agent"),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ""])

    assert main_module.main() == 2
    assert "empty question" in capsys.readouterr().err


def test_whitespace_only_ask_is_treated_the_same(tmp_path, monkeypatch, capsys):
    """A question of spaces is not a question, and `.strip()` is what says so."""
    monkeypatch.setattr(
        app_module, "run_one_shot",
        lambda *a, **k: pytest.fail("whitespace must not reach one-shot"),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "   "])

    assert main_module.main() == 2
    assert "empty question" in capsys.readouterr().err


def test_empty_file_hint_exits_two_before_building_an_agent(tmp_path, monkeypatch, capsys):
    """`--file ""` never reached the check: `if not file_hint` returned "fine".

    Measured 2026-08-05 before the fix: passed preflight, agent built, run
    proceeded with a hint that named nothing.
    """
    monkeypatch.setattr(
        app_module, "build_agent",
        lambda *a, **k: pytest.fail("an empty --file must not build an agent"),
    )
    monkeypatch.setattr(
        app_module, "run_one_shot",
        lambda *a, **k: pytest.fail("an empty --file must not reach one-shot"),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["main.py", "--workspace", str(tmp_path), "--file", "", "--ask", "q"],
    )

    assert main_module.main() == 2
    assert "empty path" in capsys.readouterr().err


def test_whitespace_file_hint_exits_two(tmp_path, monkeypatch, capsys):
    """`--file "   "` stripped to "", and `Path("")` resolves to the workspace,
    which exists — so it passed as a valid file hint."""
    monkeypatch.setattr(
        app_module, "build_agent",
        lambda *a, **k: pytest.fail("whitespace --file must not build an agent"),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["main.py", "--workspace", str(tmp_path), "--file", "   ", "--ask", "q"],
    )

    assert main_module.main() == 2
    assert "empty path" in capsys.readouterr().err


def test_directory_file_hint_exits_two_and_says_it_is_a_directory(
    tmp_path, monkeypatch, capsys
):
    """A directory passed because the check asked `exists()`, not `is_file()`.

    The message names the reason: "is a directory" and "does not exist" are
    different faults and must not read the same.
    """
    (tmp_path / "adir").mkdir()
    monkeypatch.setattr(
        app_module, "build_agent",
        lambda *a, **k: pytest.fail("a directory --file must not build an agent"),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["main.py", "--workspace", str(tmp_path), "--file", "adir", "--ask", "q"],
    )

    assert main_module.main() == 2
    err = capsys.readouterr().err
    assert "is a directory" in err
    assert "does not exist" not in err
