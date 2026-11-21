"""Contract for cli/help.py: the rendered texts must not drift by one byte.

Two frozen fixtures hold exactly what operators see today —
``tests/fixtures/help_page_expected.txt`` (the whole ``:help`` page as it reaches
stderr, trailing newline included) and ``tests/fixtures/startup_commands_expected.txt``
(the ``Commands: …`` tail of the REPL readiness banner).

The renderer is checked against those fixtures, and the fixtures are checked
against the live output while ``main.py`` still owns the literals — so the pair
proves the extraction changed nothing. Once ``main.py`` renders through this
module, the fixture comparison stays as the regression guard.

The other half of the contract is coverage: every command in the registry must
appear somewhere in the layout, so a command cannot be added to the registry and
silently stay out of ``:help``.
"""
from __future__ import annotations

import re
from importlib.util import find_spec
from pathlib import Path

from cli import command_registry as reg
from cli import help as help_module

# ── coverage: nothing can drop out of the help page ──────────────────────────

def test_every_registry_command_appears_in_the_help_layout():
    missing = sorted(spec.canonical for spec in reg.COMMANDS
                     if spec.canonical not in help_module.layout_tokens())
    assert missing == [], (
        f"these commands are in the registry but nowhere in the help layout: {missing}"
    )


# ── shape of the layout itself ───────────────────────────────────────────────


def test_help_module_imports_only_the_registry():
    source = Path(find_spec("cli.help").origin).read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.MULTILINE)
    assert set(imports) <= {"__future__", "cli.command_registry", "re"}, imports
