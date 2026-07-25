"""C11 — an exact snapshot of the duplicated command surfaces.

At 9daa9bf the operator-command surface is described in **four** places that are
maintained by hand and already disagree:

1. the `head` chain in `handle_meta_command` (what actually dispatches);
2. the two pre-dotenv one-shot fast paths in `main()`;
3. the `:help` page;
4. the REPL startup banner.

Plus two adjacent mechanisms that are *not* head-dispatched commands: REPL block
control tokens (`:task-begin`/`:task-end`/`:task-abort`/`:end`) and the
natural-language operator intents.

This module derives each set from the code and freezes the exact counts and the
exact differences. It deliberately does **not** assert that everything printed by
`:help` or the banner is a head-dispatched command — several entries are block
tokens, headings, flag continuation lines or prose. Phase 2 replaces these
duplicates with one registry; these numbers are what it must reproduce or
consciously change.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

# Captured before any monkeypatch replaces the attribute (see the reader factory
# in _startup_tokens, which must build the real class, not its own stand-in).
_REAL_STDIN_READER = main_module._StdinLineReader

# Frozen snapshot at 9daa9bf. A diff here means a surface moved — update this
# table together with docs/refactor/CLI_BASELINE.md.
FROZEN = {
    "dispatched": 140,
    "pre_dotenv_fast_paths": 2,
    "repl_control_tokens": 4,
    "help_tokens": 96,
    # 72 tokens are printed, but one of them (`:task-begin`) is a REPL block
    # token rather than a dispatched command — see the divergence tests below.
    "startup_tokens": 72,
    "startup_dispatched_tokens": 71,
    "nl_intent_kinds": 23,
}

_STANDALONE_TOKEN = re.compile(r"(?<![\w-])(:[a-z][a-z0-9-]*)(?![\w-])")


def _dispatched() -> set[str]:
    """Head-chain tokens, via the existing guard's own parser (not re-derived)."""
    script = REPO_ROOT / "scripts" / "commands_map_check.py"
    spec = importlib.util.spec_from_file_location("commands_map_check_snapshot", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.dispatched_commands(MAIN_SOURCE)


def _pre_dotenv_fast_paths() -> set[str]:
    """Commands matched with `head.lower() == …` before `load_dotenv()` runs."""
    marker = "\n    load_dotenv()"
    assert marker in MAIN_SOURCE, "load_dotenv() call site moved"
    prefix = MAIN_SOURCE.split(marker, 1)[0]
    return set(re.findall(r'head\.lower\(\)\s*==\s*"(:[a-z0-9-]+)"', prefix))


def _repl_control_tokens() -> set[str]:
    """Block/buffer tokens the REPL intercepts itself (never head-dispatched)."""
    tokens = set(re.findall(r'q\s*==\s*"(:[a-z0-9-]+)"', MAIN_SOURCE))
    tokens |= set(re.findall(r'marker\s*==\s*"(:[a-z0-9-]+)"', MAIN_SOURCE))
    tokens |= set(re.findall(r'\.lower\(\)\s*==\s*"(:end)"', MAIN_SOURCE))
    return tokens


def _nl_intent_kinds() -> set[str]:
    """`intent.kind` branches inside `_dispatch_operator_intent` only."""
    start = MAIN_SOURCE.index("def _dispatch_operator_intent(")
    end = MAIN_SOURCE.index("def _collect_instruction_buffer(")
    return set(re.findall(r'intent\.kind == "([a-z_]+)"', MAIN_SOURCE[start:end]))


def _help_tokens(tmp_path: Path, capsys) -> set[str]:
    """Tokens the live `:help` page prints (it needs no agent state)."""
    assert main_module.handle_meta_command(":help", SimpleNamespace(), tmp_path) is True
    return set(_STANDALONE_TOKEN.findall(capsys.readouterr().err))


def _startup_tokens(tmp_path: Path, monkeypatch, capsys) -> set[str]:
    """Tokens the live REPL banner prints after `Commands:`."""
    pending: list[str] = []

    def readline() -> str:
        return ""  # immediate EOF

    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(
        main_module,
        "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(main_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(
        main_module,
        "_StdinLineReader",
        lambda **k: _REAL_STDIN_READER(interactive=False, readline=readline),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])
    assert main_module.main() == 0
    err = capsys.readouterr().err
    assert "Commands:" in err
    banner = err.split("Commands:", 1)[1]
    assert pending == []
    return set(_STANDALONE_TOKEN.findall(banner))


# ── exact sizes ───────────────────────────────────────────────────────────────

def test_dispatched_command_count_is_frozen():
    assert len(_dispatched()) == FROZEN["dispatched"]


def test_pre_dotenv_fast_paths_are_exactly_two():
    fast = _pre_dotenv_fast_paths()
    assert fast == {":self-build-propose", ":schedule-disable"}
    assert len(fast) == FROZEN["pre_dotenv_fast_paths"]


def test_repl_control_tokens_are_frozen():
    control = _repl_control_tokens()
    assert control == {":operator-task", ":task-begin", ":task-end", ":task-abort", ":end"}
    # `:operator-task` genuinely overlaps: the REPL intercepts it as a block AND
    # `handle_meta_command` dispatches it. The other four are REPL-only.
    assert ":operator-task" in _dispatched()
    assert len(control - _dispatched()) == FROZEN["repl_control_tokens"]


def test_help_and_startup_token_counts_are_frozen(tmp_path, monkeypatch, capsys):
    assert len(_help_tokens(tmp_path, capsys)) == FROZEN["help_tokens"]
    assert len(_startup_tokens(tmp_path, monkeypatch, capsys)) == FROZEN["startup_tokens"]


def test_nl_intent_kind_count_is_frozen():
    kinds = _nl_intent_kinds()
    assert len(kinds) == FROZEN["nl_intent_kinds"]
    # Four intents target handlers that have no `:command` equivalent at all.
    assert {
        "capability_check",
        "current_gaps_check",
        "weakness_finder",
        "next_safe_test",
    } <= kinds


def test_shell_command_hint_is_handled_before_dispatch_and_is_not_a_command():
    """`shell_command_hint` short-circuits in `handle_conversational_operator_input`."""
    assert "shell_command_hint" not in _nl_intent_kinds()
    assert 'intent.kind == "shell_command_hint"' in MAIN_SOURCE


# ── exact divergences ─────────────────────────────────────────────────────────

def test_help_page_tokens_that_are_not_dispatched_commands(tmp_path, capsys):
    """Recorded: the help page also documents REPL block tokens."""
    extra = _help_tokens(tmp_path, capsys) - _dispatched()
    assert extra == {":task-begin", ":task-end", ":task-abort", ":end"}


def test_startup_banner_advertises_one_non_dispatched_token(tmp_path, monkeypatch, capsys):
    """Recorded: the banner mixes a REPL block token in with real commands.

    `:task-begin` is intercepted by the REPL loop, not by `handle_meta_command`,
    so a registry cannot treat every banner entry as a dispatchable command.
    """
    startup_tokens = _startup_tokens(tmp_path, monkeypatch, capsys)
    assert startup_tokens - _dispatched() == {":task-begin"}
    assert len(startup_tokens) == FROZEN["startup_tokens"]
    assert len(startup_tokens & _dispatched()) == FROZEN["startup_dispatched_tokens"]


def test_startup_banner_is_not_a_subset_of_the_help_page(tmp_path, monkeypatch, capsys):
    """Recorded: the banner advertises `:help`, but the help page omits itself."""
    help_tokens = _help_tokens(tmp_path, capsys)
    startup_tokens = _startup_tokens(tmp_path, monkeypatch, capsys)
    assert not startup_tokens <= help_tokens
    assert startup_tokens - help_tokens == {":help"}
    assert ":help" in _dispatched()


def test_commands_absent_from_the_startup_banner_are_counted(tmp_path, monkeypatch, capsys):
    """The banner documents far less than the dispatcher accepts."""
    missing = _dispatched() - _startup_tokens(tmp_path, monkeypatch, capsys)
    # A representative, stable sample of the gap (full count asserted below).
    assert {":clear", ":hygiene", ":rollback", ":inbox", ":assumptions", ":exit"} <= missing
    assert len(missing) == FROZEN["dispatched"] - FROZEN["startup_dispatched_tokens"]


def test_commands_absent_from_the_help_page_are_aliases(tmp_path, capsys):
    """Recorded: `:help` omits many working aliases the dispatcher accepts."""
    missing = _dispatched() - _help_tokens(tmp_path, capsys)
    assert {":memory-status", ":reset", ":kill-switch", ":assumption-log"} <= missing


def test_question_mark_is_a_help_alias_outside_the_colon_namespace():
    assert 'head in {":help", "?"}' in MAIN_SOURCE
    assert '":help", "?"' in MAIN_SOURCE
    # `?` cannot appear in any `:token` set, so a registry must model it apart.
    assert "?" not in _dispatched()


def test_help_page_carries_non_command_prose(tmp_path, capsys):
    """Headings, the `empty line` note and shortcut prose are not commands."""
    assert main_module.handle_meta_command(":help", SimpleNamespace(), tmp_path) is True
    text = capsys.readouterr().err
    assert "Commands:" in text
    assert "Conversational shortcuts:" in text
    assert "empty line" in text
    assert "flags:" in text
    # The Russian conversational shortcuts are prose, not tokens.
    assert "Проверь проект" in text
    assert not _STANDALONE_TOKEN.findall("Проверь проект и скажи что требует внимания")
