"""Read-only tests for scripts/commands_map_check.py (registry <-> doc parity).

The guard compares ``cli/command_registry.py`` against ``docs/COMMANDS_MAP.md`` in
both directions. It deliberately no longer derives its verdict from ``main.py``:
the old whole-file scan would have kept passing once dispatch moved out of that
file. The code-to-registry link is asserted separately by
``tests/test_command_registry.py``; ``dispatched_commands()`` remains here as the
shared parser for that link.

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


# ── the parity contract ──────────────────────────────────────────────────────

def test_every_registry_command_is_documented():
    mod = _load_module()
    missing = mod.undocumented_commands()
    assert missing == [], f"registry commands missing from COMMANDS_MAP.md: {missing}"


def test_document_lists_no_command_the_registry_lacks():
    mod = _load_module()
    unknown = mod.unknown_documented_commands()
    assert unknown == [], f"documented commands absent from the registry: {unknown}"


def test_main_returns_zero():
    mod = _load_module()
    assert mod.main() == 0


def test_verdict_comes_from_the_registry_not_from_main_py():
    """Guards the regression the rewrite fixed: a `main.py`-derived verdict would
    silently pass once dispatch moves out of that file."""
    mod = _load_module()
    from cli.command_registry import all_tokens

    assert mod.registry_commands() == set(all_tokens())
    source = open(_SCRIPT, encoding="utf-8").read()
    # dispatched_commands is kept as a helper, but must not feed the verdict
    verdict_body = source.split("def main(", 1)[1]
    assert "dispatched_commands" not in verdict_body


# ── document parsing ─────────────────────────────────────────────────────────

def test_only_table_rows_count_as_documentation():
    """Prose that merely mentions a command name does not document it."""
    mod = _load_module()
    prose = "Enter these at the REPL, e.g. `:command args` or :commands.\n"
    assert mod.documented_commands(prose) == set()


def test_aliases_on_one_row_are_all_documented():
    mod = _load_module()
    row = "| `:clear \\| :reset` | Clear working memory. |\n"
    assert mod.documented_commands(row) == {":clear", ":reset"}


def test_escaped_pipes_inside_a_usage_sketch_are_not_aliases():
    """`[on\\|off\\|status]` is one sketch, not three aliases."""
    mod = _load_module()
    row = "| `:audit [on\\|off\\|status]` | Read-only audit mode. |\n"
    assert mod.documented_commands(row) == {":audit"}


def test_question_mark_alias_is_skipped():
    mod = _load_module()
    row = "| `:help \\| ?` | Show command help. |\n"
    assert mod.documented_commands(row) == {":help"}


def test_standalone_token_not_satisfied_by_substring():
    mod = _load_module()
    row = "| `:memory-status` | show |\n"
    documented = mod.documented_commands(row)
    assert documented == {":memory-status"}
    assert ":memory" not in documented


# ── drift is detectable in both directions ───────────────────────────────────

def test_a_command_missing_from_the_document_is_detectable():
    mod = _load_module()
    registry = mod.registry_commands()
    documented = mod.documented_commands("| `:clear` | x |\n")
    assert registry - documented, "set math must flag undocumented commands"
    assert ":clear" not in (registry - documented)


def test_a_documented_command_the_registry_lacks_is_detectable():
    mod = _load_module()
    documented = mod.documented_commands("| `:ghost-command` | x |\n")
    assert documented - mod.registry_commands() == {":ghost-command"}


# ── the shared dispatch parser stays available ───────────────────────────────

def test_dispatch_extraction_handles_both_forms():
    mod = _load_module()
    src = 'if head == ":alpha":\n    ...\nif head in {":beta", ":gamma"}:\n    ...'
    assert {":alpha", ":beta", ":gamma"} <= mod.dispatched_commands(src)


def test_dispatch_parser_still_matches_the_live_registry():
    """The dispatch chain (now in cli/command_dispatch.py) and the registry must
    agree exactly."""
    mod = _load_module()
    dispatch_source = open(
        os.path.join(_ROOT, "cli", "command_dispatch.py"), encoding="utf-8"
    ).read()
    assert mod.dispatched_commands(dispatch_source) == mod.registry_commands()


def test_script_is_read_only():
    with open(_SCRIPT, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
