#!/usr/bin/env python3
"""Read-only drift check for docs/AGENT_ANATOMY.md (TD-029).

This script is intentionally inert. It ONLY:
  * lists files under core/ (os.listdir),
  * reads the text of docs/AGENT_ANATOMY.md,
  * prints a report and returns an exit code.

It deliberately does NOT, and must never:
  * import or execute any agent/core module,
  * call an LLM/provider or any network,
  * call git,
  * write, create, or modify any file.

It verifies that the "Module index" in docs/AGENT_ANATOMY.md stays in sync with
the actual core/ package, so future drift is visible instead of silent.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(ROOT, "core")
DOC_PATH = os.path.join(ROOT, "docs", "AGENT_ANATOMY.md")

# Modules that live in core/ but are intentionally excluded from the map index.
_EXCLUDED = {"__init__"}


def _core_modules() -> set[str]:
    """Names of core/*.py modules, without extension. File listing only."""
    names: set[str] = set()
    for entry in os.listdir(CORE_DIR):
        if entry.endswith(".py"):
            stem = entry[:-3]
            if stem not in _EXCLUDED:
                names.add(stem)
    return names


def _documented_modules(text: str) -> set[str]:
    """Every `core/<name>` token referenced in the anatomy document."""
    return set(re.findall(r"core/([a-zA-Z0-9_]+)", text))


def main() -> int:
    if not os.path.isdir(CORE_DIR):
        print(f"ERROR: core/ directory not found at {CORE_DIR}")
        return 2
    if not os.path.isfile(DOC_PATH):
        print(f"ERROR: anatomy map not found at {DOC_PATH}")
        return 2

    actual = _core_modules()
    with open(DOC_PATH, "r", encoding="utf-8") as handle:
        text = handle.read()
    documented = _documented_modules(text)

    missing = sorted(actual - documented)  # in core/, absent from the map
    stale = sorted(documented - actual)    # named in the map, not in core/

    print("Agent anatomy map drift check (read-only)")
    print(f"  core/ modules:      {len(actual)}")
    print(f"  documented modules: {len(documented & actual)}")

    if missing:
        print(f"\nMISSING from docs/AGENT_ANATOMY.md ({len(missing)}):")
        for name in missing:
            print(f"  - core/{name}")
    if stale:
        print(f"\nSTALE in docs/AGENT_ANATOMY.md (no such core/ module) ({len(stale)}):")
        for name in stale:
            print(f"  - core/{name}")

    if missing or stale:
        print("\nRESULT: OUT OF SYNC - update docs/AGENT_ANATOMY.md.")
        return 1

    print("\nRESULT: in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
