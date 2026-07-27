"""Prove the documentation still matches the code — read-only, exit non-zero on drift.

The other doc guards each cover one axis: `docs_link_check.py` resolves relative
Markdown links, `agent_anatomy_check.py` keeps the `core/` module index in sync,
`commands_map_check.py` checks registry <-> COMMANDS_MAP parity,
`registry_tally.py` checks the issue tally. None of them look at the *code
references embedded in prose*, which is where most documentation claims live:
"``core/loop.py`` does X", "see ``cli/app.py:69``", "``:self-apply-run`` applies".

This script extracts those references from every Markdown file under `docs/` and
verifies each one against the working tree:

* **paths** — does the referenced file exist?
* **line anchors** (`file.py:123`) — is the line within the file's current length?
  A stale anchor is reported as INFO, not an error, when the document declares it
  as historical provenance (see the `_HISTORICAL_ANCHOR_DOCS` allowlist), because
  those anchors intentionally point at an old commit.
* **`:command` tokens** — is the command in `cli/command_registry.py`?

Anything unresolved is printed with its file and line so it can be fixed or
declared. Nothing is written.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

#: Documents that deliberately keep line anchors from an older commit as
#: provenance. They say so in their own text; a stale anchor there is expected.
_HISTORICAL_ANCHOR_DOCS = {
    "refactor/CLI_BASELINE.md",
    "audit/MASTER_ISSUE_REGISTRY.md",
    "audit/MEMORY_MAP.md",
    "audit/MEMORY_LIFECYCLE_CONTRACT.md",
    "LIVE_PROBE_FINDINGS.md",
    "MEMORY_SYSTEM_AUDIT.md",
    "MEMORY_FIX_PLAN.md",
    "audit/PROVIDER_STRUCTURED_OUTPUT_AUDIT.md",
    "audit/PROVIDER_AUDIT_CHECKPOINT.md",
    "audit/AUDIT_PROGRESS.md",
    "self-audit-lessons.md",
    "CORE_AUDIT_2026-07-18.md",
    "daemon-progress.md",
}

#: Top-level code directories a documented path may start with.
_CODE_ROOTS = ("core", "cli", "app", "api", "tools", "tests", "scripts", "bug_lab",
               "project_intelligence")

_PATH_RE = re.compile(
    r"(?<![\w/.])((?:" + "|".join(_CODE_ROOTS) + r")(?:/[\w.\-]+)+\.py)(?::(\d+))?"
)
_COMMAND_RE = re.compile(r"(?<![\w:])(:[a-z][a-z0-9-]{2,})(?![\w-])")

#: Tokens that look like commands in prose but are not dispatched commands.
#: The four REPL block tokens are intercepted by the dialogue loop before
#: dispatch and are deliberately absent from the registry — pinned by
#: ``tests/characterization/test_command_surface_snapshot.py`` ("repl_control
#: _tokens": 4). The rest are generic placeholders ("the :command surface").
_NON_COMMAND_TOKENS = {
    ":task-begin", ":task-end", ":task-abort", ":end",   # REPL block tokens
    ":command", ":commands", ":token",                    # prose placeholders
}

#: A path introduced by one of these words is a file the document says does NOT
#: exist yet (proposed/planned test, missing coverage). Referencing it is
#: correct documentation, not drift.
_PLANNED_MARKERS = ("proposed", "missing test", "planned", "should be added", "to be written")

#: Files that were RENAMED, old path -> new path.
#:
#: A dated audit document that says "the defect was in `core/foo.py`" stays true
#: after `foo.py` is renamed — the finding happened to that file, under that
#: name. Rewriting the sentence would falsify the record; leaving the reference
#: unresolvable would make the guard useless. So the rename is declared once,
#: here, and this table is the single place that records it. A path that is
#: merely missing (deleted, never written, mistyped) still fails.
#:
#: Add an entry only for a real rename, and only together with the commit that
#: performs it.
_RENAMED_PATHS: dict[str, str] = {
    # Renamed 2026-07-27: the module stopped computing "confidence" and started
    # reporting evidence support with an explicit applicability flag, after
    # measurement showed the old scalar conflated three different situations
    # (docs/audit/SENSOR_SIGNAL_MEASUREMENT.md).
    "core/confidence_gate.py": "core/evidence_support.py",
    "tests/test_confidence_gate.py": "tests/test_evidence_support.py",
}


def _registry_commands() -> set[str]:
    src = (REPO / "cli" / "command_registry.py").read_text(encoding="utf-8")
    return set(re.findall(r'canonical="(:[a-z0-9-]+)"', src)) | set(
        re.findall(r'"(:[a-z0-9-]+)"', src)
    )


def main() -> int:
    commands = _registry_commands()
    line_counts: dict[Path, int] = {}
    missing_paths: list[str] = []
    stale_anchors: list[str] = []
    historical_anchors = 0
    planned_paths = 0
    renamed_paths = 0
    unknown_commands: list[str] = []
    checked_paths = checked_anchors = checked_commands = 0

    for doc in sorted(DOCS.rglob("*.md")):
        rel_doc = doc.relative_to(DOCS).as_posix()
        historical = rel_doc in _HISTORICAL_ANCHOR_DOCS
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PATH_RE.finditer(line):
                path_text, anchor = match.group(1), match.group(2)
                target = REPO / path_text
                checked_paths += 1
                if not target.is_file():
                    lowered = line.lower()
                    if path_text in _RENAMED_PATHS:
                        # The record is about the file under its old name; the
                        # rename is declared, so the reference still resolves.
                        renamed = REPO / _RENAMED_PATHS[path_text]
                        if renamed.is_file():
                            renamed_paths += 1
                            continue
                        missing_paths.append(
                            f"{rel_doc}:{lineno}  {path_text} "
                            f"(declared renamed to {_RENAMED_PATHS[path_text]}, "
                            f"which does not exist either)"
                        )
                    elif any(marker in lowered for marker in _PLANNED_MARKERS):
                        planned_paths += 1   # documented as not existing yet
                    else:
                        missing_paths.append(f"{rel_doc}:{lineno}  {path_text}")
                    continue
                if anchor:
                    checked_anchors += 1
                    if target not in line_counts:
                        line_counts[target] = len(
                            target.read_text(encoding="utf-8", errors="replace").splitlines()
                        )
                    if int(anchor) > line_counts[target]:
                        if historical:
                            historical_anchors += 1
                        else:
                            stale_anchors.append(
                                f"{rel_doc}:{lineno}  {path_text}:{anchor} "
                                f"(file has {line_counts[target]} lines)"
                            )
            for match in _COMMAND_RE.finditer(line):
                token = match.group(1)
                if token in _NON_COMMAND_TOKENS or token.endswith("-"):
                    continue
                checked_commands += 1
                if token not in commands:
                    unknown_commands.append(f"{rel_doc}:{lineno}  {token}")

    print("Docs <-> code conformance check (read-only)")
    print(f"  documents scanned      : {len(list(DOCS.rglob('*.md')))}")
    print(f"  code paths referenced  : {checked_paths}  "
          f"(missing: {len(missing_paths)}, declared not-yet-written: {planned_paths}, "
          f"declared renamed: {renamed_paths})")
    print(f"  line anchors checked   : {checked_anchors}  "
          f"(out of range: {len(stale_anchors)}, declared historical: {historical_anchors})")
    print(f"  :command tokens        : {checked_commands}  (unknown: {len(set(unknown_commands))})")

    failed = False
    if missing_paths:
        failed = True
        print("\n  MISSING PATHS — the document points at a file that does not exist:")
        for item in missing_paths:
            print(f"    {item}")
    if stale_anchors:
        failed = True
        print("\n  STALE LINE ANCHORS in documents not declared historical:")
        for item in stale_anchors:
            print(f"    {item}")
    if unknown_commands:
        failed = True
        print("\n  UNKNOWN COMMANDS — documented but not in the registry:")
        for item in sorted(set(unknown_commands)):
            print(f"    {item}")

    print("\n  RESULT:", "DRIFT FOUND" if failed else "every code reference resolves.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
