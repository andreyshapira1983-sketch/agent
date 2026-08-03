"""Read-only guard: the command registry and docs/COMMANDS_MAP.md must agree.

``docs/COMMANDS_MAP.md`` claims to be the authoritative map of the real operator
command surface ("if a command is not here, it does not exist"). This guard holds
it to that claim by comparing it against ``cli/command_registry.py`` in **both**
directions: every command in the registry must be documented, and the document
must not list a command the registry does not have.

Why the registry and not ``main.py``
------------------------------------
This check used to scan ``main.py`` for the ``head ==`` / ``head in {...}``
dispatch chain. That made it silently useless the moment dispatch moved out of
``main.py``: the extracted token set would shrink and the comparison would keep
*passing*. The registry is the stable description of the surface, and it is tied
to the running code by ``tests/test_command_registry.py``, which re-derives the
dispatch chain and requires an exact match. So the chain of custody is:

    dispatch chain in main.py  --(tests/test_command_registry.py)-->  registry
    registry  --(this script)-->  docs/COMMANDS_MAP.md

``dispatched_commands()`` below is still the shared parser for the first link and
is used by those tests; this script's own verdict no longer depends on it.

Contract / non-goals
--------------------
- Only the first column of the ``COMMANDS_MAP`` tables is read — the backticked
  signature cell. Command names mentioned in prose are not treated as
  documentation (the older whole-file scan wrongly picked up ``:command`` and
  ``:commands`` from an example).
- An escaped pipe separates aliases in that cell, but a usage sketch such as
  ``[on\\|off\\|status]`` uses the same escape, so a part only starts a new alias
  when it begins with a real token.
- ``?`` is documented as an alias of ``:help`` but lives outside the ``:token``
  namespace; the registry models it separately and it is skipped here.
- Pure read-only: it reads two files and imports one pure-data module. No
  runtime import, no shell-out, no network access.

Exit 0 when both directions agree; exit 1 (and print the offenders) otherwise.
Wired into CI via ``tests/test_commands_map_check.py``.
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAP = os.path.join(_ROOT, "docs", "COMMANDS_MAP.md")

if _ROOT not in sys.path:  # allow `python scripts/commands_map_check.py`
    sys.path.insert(0, _ROOT)

from cli.command_registry import all_tokens  # noqa: E402  (needs sys.path above)

_CMD = r":[a-z0-9][a-z0-9-]*"
_EQ_RE = re.compile(r'head\s*==\s*"(' + _CMD + r')"')
_IN_RE = re.compile(r"head\s+in\s*\{([^}]*)\}")
_TOKEN_IN_SET_RE = re.compile(r'"(' + _CMD + r')"')
_ROW_TOKEN_RE = re.compile(r"^(" + _CMD + r")(?:\s|$)")


def dispatched_commands(source: str) -> set[str]:
    """Command tokens dispatched via the ``head`` chain in ``source``.

    Kept as the shared parser for the code-to-registry link that
    ``tests/test_command_registry.py`` and the characterization snapshot assert.
    This script's verdict does not use it.
    """
    cmds: set[str] = set(_EQ_RE.findall(source))
    for block in _IN_RE.findall(source):
        cmds.update(_TOKEN_IN_SET_RE.findall(block))
    return cmds


def documented_commands(doc: str) -> set[str]:
    """Tokens declared in the signature cell of a ``COMMANDS_MAP`` table row."""
    documented: set[str] = set()
    for raw in doc.splitlines():
        if not raw.startswith("| `:"):
            continue
        cell = raw.strip().strip("|").split(" | ")[0].strip().strip("`")
        for part in (p.strip().strip("`").strip() for p in cell.split(r"\|")):
            if not part or part == "?":
                continue
            match = _ROW_TOKEN_RE.match(part)
            if match:
                documented.add(match.group(1))
    return documented


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def registry_commands() -> set[str]:
    """Every token the registry accepts, canonical and alias alike."""
    return set(all_tokens())


def undocumented_commands() -> list[str]:
    """Registry commands missing from ``COMMANDS_MAP.md``."""
    return sorted(registry_commands() - documented_commands(_read(_MAP)))


def unknown_documented_commands() -> list[str]:
    """Commands the document lists that the registry does not have."""
    return sorted(documented_commands(_read(_MAP)) - registry_commands())


def main() -> int:
    registry = registry_commands()
    documented = documented_commands(_read(_MAP))
    missing = sorted(registry - documented)
    unknown = sorted(documented - registry)

    print("Registry <-> COMMANDS_MAP parity check (read-only)")
    print(f"  registry commands:   {len(registry)}")
    print(f"  documented commands: {len(documented)}")
    if not missing and not unknown:
        print("  RESULT: both directions agree.")
        return 0
    if missing:
        print(f"  {len(missing)} registry command(s) missing from COMMANDS_MAP.md:")
        for cmd in missing:
            print(f"    {cmd}")
    if unknown:
        print(f"  {len(unknown)} documented command(s) absent from the registry:")
        for cmd in unknown:
            print(f"    {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
