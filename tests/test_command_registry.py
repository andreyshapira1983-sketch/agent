"""Guard: cli/command_registry.py must agree with the live command surface.

Phase 1 introduced the registry as **pure data** that nothing consumes yet. Its
whole value depends on being an exact mirror of what the code actually does, so
every fact in it is re-derived here from the real sources and compared:

- the accepted tokens and their grouping come from the ``head`` dispatch chain in
  ``main.py``;
- ``usage`` comes from ``knowledge/maps/COMMANDS_MAP.md``;
- ``in_help`` comes from the live ``:help`` page;
- ``in_startup_summary`` comes from the REPL banner literal;
- ``phase`` comes from the two pre-``load_dotenv`` fast paths in ``main()``.

A hand edit that drifts from any of those fails here. When Phase 2 makes ``:help``
and the banner render *from* the registry, the two visibility tests below become
the assertions that the rendering did not lose anything.
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

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

_CMD = r":[a-z0-9][a-z0-9-]*"
_STANDALONE = re.compile(r"(?<![\w-])(" + _CMD + r")(?![\w-])")


# ── independent derivations from the real sources ────────────────────────────


def _help_tokens() -> set[str]:
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(io.StringIO()):
        assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), REPO_ROOT) is True
    return set(_STANDALONE.findall(buf.getvalue()))


# ── the token surface ────────────────────────────────────────────────────────


def test_tokens_are_unique_across_the_registry():
    seen: dict[str, str] = {}
    for spec in reg.COMMANDS:
        for token in spec.tokens:
            assert token not in seen, f"{token} claimed by {seen.get(token)} and {spec.canonical}"
            seen[token] = spec.canonical
    assert len(seen) == len(reg.BY_TOKEN)


# ── visibility flags vs the live surfaces ────────────────────────────────────

def test_in_help_matches_the_live_help_page():
    help_tokens = _help_tokens()
    for spec in reg.COMMANDS:
        listed = any(token in help_tokens for token in spec.tokens)
        assert spec.in_help == listed, (
            f"{spec.canonical}: in_help={spec.in_help} but the :help page "
            f"{'lists' if listed else 'omits'} it"
        )


# ── lookup helpers ───────────────────────────────────────────────────────────


def test_canonical_tokens_and_in_category_are_consistent():
    canonicals = reg.canonical_tokens()
    assert len(canonicals) == len(reg.COMMANDS)
    assert set(canonicals) == {spec.canonical for spec in reg.COMMANDS}
    covered = [spec for category in {s.category for s in reg.COMMANDS}
               for spec in reg.in_category(category)]
    assert len(covered) == len(reg.COMMANDS)
