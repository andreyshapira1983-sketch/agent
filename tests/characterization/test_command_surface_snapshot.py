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

import cli.app as app_module
import cli.command_dispatch as dispatch_module
import cli.repl as repl_module
import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
# The `head` dispatch chain moved to cli/command_dispatch.py (Phase 3); the
# pre-load_dotenv() fast paths, the REPL block tokens and the intent bridge are
# still in main.py, so both sources are read here.
DISPATCH_SOURCE = Path(dispatch_module.__file__).read_text(encoding="utf-8")

# Captured before any monkeypatch replaces the attribute (see the reader factory
# in _startup_tokens, which must build the real class, not its own stand-in).
_REAL_STDIN_READER = repl_module._StdinLineReader

# Frozen snapshot at 9daa9bf. A diff here means a surface moved — update this
# table to match.
FROZEN = {
    # 141 -> 142 (2026-08-15): `:causal`, водитель причинной лестницы.
    # 142 -> 144 (2026-08-26): `:standing-grant` и её сокращение `:grant`.
    # Автомат ЧИТАЛ стоячий грант, а завести его не мог никто (MIR-166).
    # 144 -> 145 (2026-08-28): `:receipts` — накопленную улику стало чем
    # спросить (MIR-138).
    # 145 -> 146 (2026-08-30): `:self-issue-retire` — дверь человеческого
    # вердикта: реестр умел только копить, 29 вечных записей глушили весы.
    "dispatched": 146,
    "pre_dotenv_fast_paths": 2,
    "repl_control_tokens": 4,
    # 96 at 9daa9bf; 98 after the two documented help gaps were closed
    # (`:refresh-models` and `:help` itself were added to the page).
    "help_tokens": 103,  # +1: :standing-grant (2026-08-26); +1: :receipts (2026-08-28); +1: :self-issue-retire (2026-08-30)
    # One printed token (`:task-begin`) is a REPL block token rather than a
    # dispatched command — see the divergence tests below.
    # 72/71 -> 73/72 (2026-08-13): `:refresh-models` joined the banner beside
    # its read-only sibling — the visible half of the catalog pair had steered
    # the operator to a usage refusal while the catalog stayed expired (R7).
    "startup_tokens": 76,  # +1: :standing-grant (2026-08-26); +1: :self-issue-retire (2026-08-30)
    "startup_dispatched_tokens": 75,  # +1: :standing-grant (2026-08-26); +1: :self-issue-retire (2026-08-30)
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


def _pre_dotenv_source_prefix(app_source: str) -> str:
    """Everything in `run_cli` that runs BEFORE `load_dotenv` is called.

    Located through the AST, not by matching a literal load_dotenv() line.
    That literal broke the moment the call gained an argument -- the CLI now
    reads the workspace's .env instead of the launch directory's -- so the
    guard failed on a FIX, not on a regression. A boundary defined by the text
    of one line is not a boundary.
    """
    import ast

    for node in ast.walk(ast.parse(app_source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "load_dotenv"):
            return chr(10).join(app_source.splitlines()[: node.lineno - 1])
    raise AssertionError("no load_dotenv() call found in cli/app.py")


def _pre_dotenv_fast_paths() -> set[str]:
    """Commands matched with `head.lower() == …` before `load_dotenv()` runs."""
    # The startup sequence moved to cli/app.py with the rest of `main()`;
    # scanning main.py here would silently freeze an empty set.
    app_source = Path(app_module.__file__).read_text(encoding="utf-8")
    prefix = _pre_dotenv_source_prefix(app_source)
    return set(re.findall(r'head\.lower\(\)\s*==\s*"(:[a-z0-9-]+)"', prefix))


def _repl_control_tokens() -> set[str]:
    """Block/buffer tokens the REPL intercepts itself (never head-dispatched)."""
    # All three patterns now live in cli/repl.py: the dialogue loop joined the
    # stdin reader and the instruction-buffer collector there, so main.py no
    # longer contains any `q == ":…"` branch. Reading main.py here would make
    # this guard silently green.
    repl_source = Path(repl_module.__file__).read_text(encoding="utf-8")
    tokens = set(re.findall(r'q\s*==\s*"(:[a-z0-9-]+)"', repl_source))
    tokens |= set(re.findall(r'marker\s*==\s*"(:[a-z0-9-]+)"', repl_source))
    tokens |= set(re.findall(r'\.lower\(\)\s*==\s*"(:end)"', repl_source))
    return tokens


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


# ── exact divergences ─────────────────────────────────────────────────────────

def test_help_page_tokens_that_are_not_dispatched_commands(tmp_path, capsys):
    """Recorded: the help page also documents REPL block tokens."""
    extra = _help_tokens(tmp_path, capsys) - _dispatched()
    assert extra == {":task-begin", ":task-end", ":task-abort", ":end"}


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
