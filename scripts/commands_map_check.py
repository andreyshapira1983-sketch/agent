"""Read-only check that the command registry and knowledge/maps/COMMANDS_MAP.md agree both ways."""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAP = os.path.join(_ROOT, "knowledge", "maps", "COMMANDS_MAP.md")

if _ROOT not in sys.path:  # allow `python scripts/commands_map_check.py`
    sys.path.insert(0, _ROOT)

from cli.command_registry import all_tokens  # noqa: E402  (needs sys.path above)

_CMD = r":[a-z0-9][a-z0-9-]*"
_EQ_RE = re.compile(r'head\s*==\s*"(' + _CMD + r')"')
_IN_RE = re.compile(r"head\s+in\s*\{([^}]*)\}")
_TOKEN_IN_SET_RE = re.compile(r'"(' + _CMD + r')"')
_ROW_TOKEN_RE = re.compile(r"^(" + _CMD + r")(?:\s|$)")


def dispatched_commands(source: str) -> set[str]:
    """Command tokens dispatched via the ``head`` chain in ``source``."""
    cmds: set[str] = set(_EQ_RE.findall(source))
    for block in _IN_RE.findall(source):
        cmds.update(_TOKEN_IN_SET_RE.findall(block))
    return cmds


def documented_commands(doc: str) -> set[str]:
    """Tokens declared in the signature cell of a ``COMMANDS_MAP`` table row.

    Prose mentions do not count. A part after an escaped pipe is an alias only if it
    starts with a real token (usage sketches reuse that escape); ``?`` is skipped.
    """
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
    with open(path, encoding="utf-8", errors="replace") as handle:
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
