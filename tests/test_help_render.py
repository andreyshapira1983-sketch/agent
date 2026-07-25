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

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import cli.command_dispatch as dispatch_module
import main as main_module
from cli import command_registry as reg
from cli import help as help_module

REPO_ROOT = Path(main_module.__file__).resolve().parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
HELP_FIXTURE = FIXTURES / "help_page_expected.txt"
BANNER_FIXTURE = FIXTURES / "startup_commands_expected.txt"


def _live_help() -> str:
    """Exactly what ``:help`` writes to stderr."""
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(io.StringIO()):
        assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), REPO_ROOT) is True
    return buf.getvalue()


# ── byte-for-byte contract ───────────────────────────────────────────────────

def test_rendered_help_matches_the_fixture_exactly():
    expected = HELP_FIXTURE.read_text(encoding="utf-8")
    # render_help() omits the trailing newline; the caller's print() supplies it.
    assert help_module.render_help() + "\n" == expected


def test_live_help_matches_the_fixture_exactly():
    assert _live_help() == HELP_FIXTURE.read_text(encoding="utf-8")


def test_rendered_startup_commands_match_the_fixture_exactly():
    expected = BANNER_FIXTURE.read_text(encoding="utf-8")
    assert help_module.render_startup_commands() + "\n" == expected


def test_fixtures_are_stored_with_unix_newlines():
    for fixture in (HELP_FIXTURE, BANNER_FIXTURE):
        assert b"\r\n" not in fixture.read_bytes(), fixture.name


# ── coverage: nothing can drop out of the help page ──────────────────────────

def test_every_registry_command_appears_in_the_help_layout():
    missing = sorted(spec.canonical for spec in reg.COMMANDS
                     if spec.canonical not in help_module.layout_tokens())
    assert missing == [], (
        f"these commands are in the registry but nowhere in the help layout: {missing}"
    )


def test_layout_references_no_unknown_command():
    unknown = [entry[1] for entry in help_module.HELP_LAYOUT
               if entry[0] == "cmd" and reg.lookup(entry[1]) is None]
    assert unknown == []


def test_rendered_command_lines_use_the_registry_description():
    """The point of the extraction: descriptions are not spelled out twice."""
    rendered = help_module.render_help()
    checked = 0
    for entry in help_module.HELP_LAYOUT:
        if entry[0] != "cmd":
            continue
        _, canonical, left, column = entry
        spec = reg.lookup(canonical)
        assert spec is not None
        line = "  " + left + " " * (column - 2 - len(left)) + spec.description
        assert line in rendered, canonical
        checked += 1
    assert checked >= 80, f"only {checked} lines render from the registry"


def test_banner_tokens_agree_with_the_registry_flag():
    """The banner is a deliberate subset; `in_startup_summary` must match it.

    One banner entry is not a command at all: ``:task-begin`` is intercepted by
    the REPL loop, never dispatched through the head chain. That quirk is
    recorded in ``docs/refactor/CLI_BASELINE.md`` section 3.1 and preserved here
    rather than quietly cleaned up.
    """
    non_commands = {token for token in help_module.BANNER_TOKENS if reg.lookup(token) is None}
    assert non_commands == {":task-begin"}

    flagged = {spec for spec in reg.COMMANDS if spec.in_startup_summary}
    resolved = {reg.lookup(token) for token in help_module.BANNER_TOKENS} - {None}
    assert resolved == flagged
    assert len(flagged) == 71
    assert len(help_module.BANNER_TOKENS) == 72


# ── shape of the layout itself ───────────────────────────────────────────────

def test_layout_entry_kinds_are_known_and_counted():
    kinds: dict[str, int] = {}
    for entry in help_module.HELP_LAYOUT:
        kinds[entry[0]] = kinds.get(entry[0], 0) + 1
    assert set(kinds) == {"cmd", "raw", "blank"}
    # Frozen shape: 82 lines render from the registry, 44 are verbatim (24
    # `flags:` continuations, 9 irregular command lines, 2 non-command lines,
    # 7 prose lines, 2 headings), 1 blank separates the two blocks.
    assert kinds == {"cmd": 82, "raw": 44, "blank": 1}


def test_help_module_imports_only_the_registry():
    source = (REPO_ROOT / "cli" / "help.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M)
    assert set(imports) <= {"__future__", "cli.command_registry", "re"}, imports


def test_render_is_deterministic():
    assert help_module.render_help() == help_module.render_help()
    assert help_module.render_startup_commands() == help_module.render_startup_commands()
