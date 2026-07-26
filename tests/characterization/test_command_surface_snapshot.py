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

import cli.command_dispatch as dispatch_module
import cli.repl as repl_module
import cli.app as app_module
import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
# The `head` dispatch chain moved to cli/command_dispatch.py (Phase 3); the
# pre-load_dotenv() fast paths, the REPL block tokens and the intent bridge are
# still in main.py, so both sources are read here.
DISPATCH_SOURCE = (REPO_ROOT / "cli" / "command_dispatch.py").read_text(encoding="utf-8")
# the operator-intent bridge moved to cli/intent_bridge.py (Phase 3 step 6)
BRIDGE_SOURCE = (REPO_ROOT / "cli" / "intent_bridge.py").read_text(encoding="utf-8")

# Captured before any monkeypatch replaces the attribute (see the reader factory
# in _startup_tokens, which must build the real class, not its own stand-in).
_REAL_STDIN_READER = repl_module._StdinLineReader

# Frozen snapshot at 9daa9bf. A diff here means a surface moved — update this
# table together with docs/refactor/CLI_BASELINE.md.
FROZEN = {
    "dispatched": 141,
    "pre_dotenv_fast_paths": 2,
    "repl_control_tokens": 4,
    # 96 at 9daa9bf; 98 after the two documented help gaps were closed
    # (`:refresh-models` and `:help` itself were added to the page).
    "help_tokens": 99,
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
    return mod.dispatched_commands(DISPATCH_SOURCE)


def _pre_dotenv_fast_paths() -> set[str]:
    """Commands matched with `head.lower() == …` before `load_dotenv()` runs."""
    # The startup sequence moved to cli/app.py with the rest of `main()`;
    # scanning main.py here would silently freeze an empty set.
    app_source = (REPO_ROOT / "cli" / "app.py").read_text(encoding="utf-8")
    marker = "\n    load_dotenv()"
    assert marker in app_source, "load_dotenv() call site moved"
    prefix = app_source.split(marker, 1)[0]
    return set(re.findall(r'head\.lower\(\)\s*==\s*"(:[a-z0-9-]+)"', prefix))


def _repl_control_tokens() -> set[str]:
    """Block/buffer tokens the REPL intercepts itself (never head-dispatched)."""
    # All three patterns now live in cli/repl.py: the dialogue loop joined the
    # stdin reader and the instruction-buffer collector there, so main.py no
    # longer contains any `q == ":…"` branch. Reading main.py here would make
    # this guard silently green.
    repl_source = (REPO_ROOT / "cli" / "repl.py").read_text(encoding="utf-8")
    tokens = set(re.findall(r'q\s*==\s*"(:[a-z0-9-]+)"', repl_source))
    tokens |= set(re.findall(r'marker\s*==\s*"(:[a-z0-9-]+)"', repl_source))
    tokens |= set(re.findall(r'\.lower\(\)\s*==\s*"(:end)"', repl_source))
    return tokens


def _nl_intent_kinds() -> set[str]:
    """`intent.kind` branches inside `_dispatch_operator_intent` only."""
    start = BRIDGE_SOURCE.index("def _dispatch_operator_intent(")
    end = len(BRIDGE_SOURCE)
    return set(re.findall(r'intent\.kind == "([a-z_]+)"', BRIDGE_SOURCE[start:end]))


def _help_tokens(tmp_path: Path, capsys) -> set[str]:
    """Tokens the live `:help` page prints (it needs no agent state)."""
    assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), tmp_path) is True
    return set(_STANDALONE_TOKEN.findall(capsys.readouterr().err))


def _startup_tokens(tmp_path: Path, monkeypatch, capsys) -> set[str]:
    """Tokens the live REPL banner prints after `Commands:`."""
    pending: list[str] = []

    def readline() -> str:
        return ""  # immediate EOF

    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(app_module, "_print_daemon_inbox_notice", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_StdinLineReader",
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
    assert 'intent.kind == "shell_command_hint"' in BRIDGE_SOURCE


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


def test_startup_banner_is_now_a_subset_of_the_help_page(tmp_path, monkeypatch, capsys):
    """At 9daa9bf the banner advertised `:help` while the help page omitted
    itself, so the banner was *not* a subset. That gap is fixed: every token the
    banner shows is now documented on the help page too."""
    help_tokens = _help_tokens(tmp_path, capsys)
    startup_tokens = _startup_tokens(tmp_path, monkeypatch, capsys)
    assert startup_tokens - help_tokens == set()
    assert startup_tokens <= help_tokens
    assert ":help" in _dispatched()
    assert ":help" in help_tokens


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
    # 48 at 9daa9bf; 46 after `:refresh-models` and `:help` were added to the page.
    assert len(missing) == 46


def test_question_mark_is_a_help_alias_outside_the_colon_namespace():
    assert 'head in {":help", "?"}' in DISPATCH_SOURCE
    assert '":help", "?"' in DISPATCH_SOURCE
    # `?` cannot appear in any `:token` set, so a registry must model it apart.
    assert "?" not in _dispatched()


def test_help_page_carries_non_command_prose(tmp_path, capsys):
    """Headings, the `empty line` note and shortcut prose are not commands."""
    assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), tmp_path) is True
    text = capsys.readouterr().err
    assert "Commands:" in text
    assert "Conversational shortcuts:" in text
    assert "empty line" in text
    assert "flags:" in text
    # The conversational shortcuts are prose, not tokens. (They were Russian at
    # 9daa9bf and were translated to English; each phrase is verified to route to
    # the same intent as the Russian original it replaced.)
    shortcut = "Check the project and tell me what needs attention"
    assert shortcut in text
    assert not _STANDALONE_TOKEN.findall(shortcut)
