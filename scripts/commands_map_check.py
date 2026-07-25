"""Read-only guard: every REPL command dispatched in ``main.py`` is documented.

``docs/COMMANDS_MAP.md`` claims to be the authoritative map of the real operator
command surface ("if a command is not here, it does not exist"). Nothing
enforced that claim, so a command could be dropped from the map (or added to
``main.py``) without the map noticing -- exactly how ``:self-build-supervisor``
went missing from the map during a manual reconciliation pass while the command
still existed in code.

This guard extracts every command token dispatched through the REPL ``head``
chain in ``main.py`` (``head == ":x"`` and ``head in {":a", ":b"}``) and asserts
each one appears as a standalone token in ``COMMANDS_MAP.md``.

Contract / non-goals:
- Only the ``head ==`` / ``head in {...}`` dispatch is inspected. Commands
  handled by other REPL mechanisms (multi-line buffer modes such as
  ``:task-begin ... :task-end``) are out of this gate's scope by design.
- The match is by *standalone* token, so ``:mem`` is not considered documented
  merely because ``:memory-status`` appears.
- The reverse direction (documented-but-not-dispatched) is reported as INFO
  only -- aliases, argument suffixes and prose make strict reverse-parity noisy
  -- and never fails the check.
- Pure read-only: no ``core`` import, no shell-out, no network access.

Exit 0 when every dispatched command is documented; exit 1 (and print the
offenders) otherwise. Wired into CI via ``tests/test_commands_map_check.py``.
"""
from __future__ import annotations

import os
import re
from typing import List, Set

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_ROOT, "main.py")
_MAP = os.path.join(_ROOT, "docs", "COMMANDS_MAP.md")

_CMD = r":[a-z0-9][a-z0-9-]*"
_EQ_RE = re.compile(r'head\s*==\s*"(' + _CMD + r')"')
_IN_RE = re.compile(r"head\s+in\s*\{([^}]*)\}")
_TOKEN_IN_SET_RE = re.compile(r'"(' + _CMD + r')"')
# a standalone token in the doc, not a substring of a longer :command
_DOC_TOKEN_RE = re.compile(r"(?<![\w-])(" + _CMD + r")(?![\w-])")


def dispatched_commands(source: str) -> Set[str]:
    """Command tokens dispatched via the ``head`` chain in ``source``."""
    cmds: Set[str] = set(_EQ_RE.findall(source))
    for block in _IN_RE.findall(source):
        cmds.update(_TOKEN_IN_SET_RE.findall(block))
    return cmds


def documented_commands(doc: str) -> Set[str]:
    """Standalone ``:command`` tokens present in ``doc``."""
    return set(_DOC_TOKEN_RE.findall(doc))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def undocumented_commands() -> List[str]:
    """Dispatched commands that are absent from ``COMMANDS_MAP.md``."""
    dispatched = dispatched_commands(_read(_MAIN))
    documented = documented_commands(_read(_MAP))
    return sorted(dispatched - documented)


def main() -> int:
    dispatched = dispatched_commands(_read(_MAIN))
    documented = documented_commands(_read(_MAP))
    missing = sorted(dispatched - documented)
    print("COMMANDS_MAP <-> main.py parity check (read-only)")
    print(f"  dispatched commands: {len(dispatched)}")
    print(f"  documented tokens:   {len(documented)}")
    if not missing:
        print("  RESULT: every dispatched command is documented.")
        return 0
    print(f"  RESULT: {len(missing)} dispatched command(s) missing from COMMANDS_MAP.md:")
    for cmd in missing:
        print(f"    {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
