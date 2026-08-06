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
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.budget_guard as budget_guard_module
import cli.app as app_module
import cli.intent_bridge as bridge_module
import cli.repl as repl_module
import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent

# Captured before any monkeypatch replaces the attribute, so the scripted reader
# below instantiates the real class instead of recursing into its own stand-in.
_REAL_STDIN_READER = repl_module._StdinLineReader


def _fake_agent() -> SimpleNamespace:
    """Minimal stand-in: only `.log.log` is reached on these paths."""
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _scripted_reader(lines: list[str]):
    """A real `_StdinLineReader` over a scripted script, EOF after the last line.

    `interactive=False` keeps `read_message` strictly line-at-a-time, so these
    tests are deterministic; paste coalescing itself is covered by
    tests/test_paste_coalesce.py and tests/characterization/test_repl_input_modes.py.
    """
    pending = list(lines)

    def readline() -> str:
        if pending:
            return pending.pop(0) + "\n"
        return ""  # EOF

    return _REAL_STDIN_READER(interactive=False, readline=readline)


def _patch_common(monkeypatch: pytest.MonkeyPatch, build_calls: list[dict]) -> None:
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)

    def fake_build_agent(workspace, **kwargs):
        build_calls.append({"workspace": workspace, **kwargs})
        return _fake_agent()

    monkeypatch.setattr(app_module, "build_agent", fake_build_agent)


def test_ask_selects_one_shot_and_returns_zero(tmp_path, monkeypatch, capsys):
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    run_calls: list[dict] = []

    def fake_run(agent, **kwargs):
        run_calls.append(kwargs)
        return "characterized answer"

    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "baseline question"]
    )

    assert main_module.main() == 0

    assert len(build_calls) == 1
    assert len(run_calls) == 1
    assert run_calls[0]["user_question"] == "baseline question"
    assert run_calls[0]["file_hint"] is None
    assert run_calls[0]["stream"] is False
    assert run_calls[0]["deep_escalation"] is None
    assert "characterized answer" in capsys.readouterr().out


def test_no_ask_selects_repl_and_eof_returns_zero(tmp_path, monkeypatch, capsys):
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    order: list[str] = []
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice",
        lambda *a, **k: order.append("daemon_notice"),
    )
    reader_kwargs: list[dict] = []

    def factory(**kwargs):
        reader_kwargs.append(kwargs)
        return _scripted_reader([])  # immediate EOF

    monkeypatch.setattr(app_module, "_StdinLineReader", factory)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    out = capsys.readouterr()
    # The REPL builds exactly one reader (single stdin owner).
    assert len(reader_kwargs) == 1
    assert set(reader_kwargs[0]) == {"interactive"}
    # Daemon notice runs before the banner.
    assert order == ["daemon_notice"]
    assert "Agent ready." in out.err
    assert "Agent ready." not in out.out


def test_repl_builds_agent_with_memory_and_default_persistent(tmp_path, monkeypatch):
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    assert len(build_calls) == 1
    call = build_calls[0]
    assert call["with_memory"] is True
    # Observed at 9daa9bf: the REPL does NOT pass with_persistent, so the
    # app.bootstrap default (True) applies. Frozen for extraction.
    assert "with_persistent" not in call
    assert call["workspace"] == tmp_path.resolve()


def test_repl_default_auto_approve_is_interactive_cli_provider(tmp_path, monkeypatch, capsys):
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])

    assert main_module.main() == 0

    provider = build_calls[0]["approval_provider"]
    assert type(provider).__name__ == "CLIApprovalProvider"
    # The banner reports the same default surface operators see.
    err = capsys.readouterr().err
    assert "approval=CLIApprovalProvider." in err
    assert "file_hint=-" in err
    assert "memory=on  persistent=on" in err


def test_workspace_default_is_cwd_and_flag_is_resolved(tmp_path, monkeypatch):
    """`--workspace` is resolved; its default is the process CWD."""
    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path / "nested" / ".." / "nested")]
    )

    assert main_module.main() == 0
    assert build_calls[0]["workspace"] == nested.resolve()

    # Default: argparse's "." resolved against the CWD (proved without touching
    # the repository by chdir-ing into the tmp workspace).
    build_calls.clear()
    monkeypatch.chdir(nested)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert main_module.main() == 0
    assert build_calls[0]["workspace"] == nested.resolve()


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


def test_one_shot_run_writes_only_inside_the_tmp_workspace(tmp_path, monkeypatch):
    """Repo-isolation proof for this suite's `main()` runs.

    Guards `Path.open`/`Path.mkdir` and fails if anything under the repository's
    real data/, logs/ or config/ is opened for writing or created.
    """
    repo = str(REPO_ROOT).replace("\\", "/").casefold()
    runtime_dirs = ("data", "logs", "config")
    violations: list[str] = []

    def _check(path: Path) -> None:
        resolved = str(Path(path)).replace("\\", "/").casefold()
        if not resolved.startswith(repo + "/"):
            return
        tail = resolved[len(repo) + 1 :]
        if any(tail == d or tail.startswith(d + "/") for d in runtime_dirs):
            violations.append(resolved)

    original_open = Path.open
    original_mkdir = Path.mkdir

    def guarded_open(self, mode="r", *args, **kwargs):
        if any(ch in mode for ch in ("w", "a", "x", "+")):
            _check(self)
        return original_open(self, mode, *args, **kwargs)

    def guarded_mkdir(self, *args, **kwargs):
        _check(self)
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)

    build_calls: list[dict] = []
    _patch_common(monkeypatch, build_calls)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "ok")
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "baseline question"]
    )
    assert main_module.main() == 0

    monkeypatch.setattr(app_module, "_StdinLineReader", lambda **k: _scripted_reader([]))
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])
    assert main_module.main() == 0

    assert violations == [], f"suite wrote into the repository runtime paths: {violations}"


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
