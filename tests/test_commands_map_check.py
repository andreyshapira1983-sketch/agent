"""Read-only tests for scripts/commands_map_check.py (command-parity guard).

These tests load the script's pure helpers and read existing repo files only.
They do not run agent code, hit the network, or write files.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "commands_map_check.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("commands_map_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_file_exists():
    assert os.path.isfile(_SCRIPT)


def test_every_dispatched_command_is_documented():
    # The committed COMMANDS_MAP.md must cover every head-dispatched command.
    mod = _load_module()
    missing = mod.undocumented_commands()
    assert missing == [], f"dispatched commands missing from COMMANDS_MAP.md: {missing}"


def test_main_returns_zero():
    mod = _load_module()
    assert mod.main() == 0


def test_dispatch_extraction_handles_both_forms():
    mod = _load_module()
    src = 'if head == ":alpha":\n    ...\nif head in {":beta", ":gamma"}:\n    ...'
    cmds = mod.dispatched_commands(src)
    assert {":alpha", ":beta", ":gamma"} <= cmds


def test_standalone_token_not_satisfied_by_substring():
    # This is exactly why the base `:memory` command was flagged: a longer
    # `:memory-status` row must not count as documenting `:memory`.
    mod = _load_module()
    documented = mod.documented_commands("| `:memory-status` | show |")
    assert ":memory-status" in documented
    assert ":memory" not in documented


def test_missing_command_is_detectable():
    # Pure set-math proof the guard would catch a dropped command.
    mod = _load_module()
    dispatched = mod.dispatched_commands('if head == ":ghost-cmd":\n    pass')
    documented = mod.documented_commands("| `:real-cmd` | x |")
    assert ":ghost-cmd" in (dispatched - documented)


def test_script_is_read_only():
    with open(_SCRIPT, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
