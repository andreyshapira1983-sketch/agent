"""Read-only tests for scripts/commands_map_check.py (registry <-> doc parity).

The guard compares ``cli/command_registry.py`` against ``knowledge/maps/COMMANDS_MAP.md`` in
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
from importlib.util import find_spec
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "commands_map_check.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("commands_map_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    source = Path(_SCRIPT).read_text(encoding="utf-8")
    # dispatched_commands is kept as a helper, but must not feed the verdict
    verdict_body = source.split("def main(", 1)[1]
    assert "dispatched_commands" not in verdict_body


# ── drift is detectable in both directions ───────────────────────────────────

def test_a_command_missing_from_the_document_is_detectable(monkeypatch):
    """A one-row map leaves every other registry command undocumented, and main() goes red."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_read", lambda _path: "| `:clear` | x |\n")
    expected = sorted(mod.registry_commands() - {":clear"})
    assert expected and ":clear" in mod.registry_commands()
    assert mod.undocumented_commands() == expected
    assert mod.unknown_documented_commands() == []
    assert mod.main() == 1


# ── the shared dispatch parser stays available ───────────────────────────────


def test_dispatch_parser_still_matches_the_live_registry():
    """The dispatch chain (now in cli/command_dispatch.py) and the registry must
    agree exactly."""
    mod = _load_module()
    dispatch_source = Path(find_spec("cli.command_dispatch").origin).read_text(encoding="utf-8")
    assert mod.dispatched_commands(dispatch_source) == mod.registry_commands()


def test_script_is_read_only():
    with open(_SCRIPT, encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
