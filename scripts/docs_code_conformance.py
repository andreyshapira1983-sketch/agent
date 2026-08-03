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
* **renamed modules** — a path in `_RENAMED_PATHS` still resolves *as history*,
  never as live architecture. Declaring a rename used to exempt the old name
  everywhere, which let a source-of-truth document go on describing a removed
  module in the present tense while this guard reported success. Now the old name
  passes only where it is explicitly declared historical — the document is in
  `_HISTORICAL_RENAME_DOCS`, or the line carries the `<!-- historical-ref -->`
  marker for a mixed document — and it is matched in **both** spellings,
  `core/foo.py` and the extensionless `core/foo` that prose usually uses.
* **`:command` tokens** — is the command in `cli/command_registry.py`?

Anything unresolved is printed with its file and line so it can be fixed or
declared. Nothing is written.

Usage: ``python scripts/docs_code_conformance.py [--docs DIR]`` — `--docs` scans
an alternative documentation tree (used by the tests to check a temporary tree
without touching the repository's own documents).
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable, Collection, Mapping
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
    # Root-level (addressed as ../<name> per the scan-loop note): a forensic
    # audit whose anchors are provenance for its own date — loop.py alone has
    # moved by hundreds of lines since (the guard caught anchor :4077 against a
    # 4047-line file the moment the scan reached it).
    "../FABLE_AUDIT.md",
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
#: Declaring a rename here does **not** license the old name: it only says which
#: new path the old one maps to. Where the old name may still appear is decided
#: by `_HISTORICAL_RENAME_DOCS` / `_HISTORICAL_REF_MARKER` below.
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

#: Documents whose renamed-path references are provenance **by nature**: dated
#: audits, frozen findings, chronological pass logs, per-issue records. Their job
#: is to say what was true when they were written, so the old name is the correct
#: word there and rewriting it would falsify the record.
#:
#: Deliberately separate from `_HISTORICAL_ANCHOR_DOCS`: that one is about line
#: numbers drifting, this one is about a module name that no longer exists. A
#: document can need one and not the other.
_HISTORICAL_RENAME_DOCS = {
    "CORE_AUDIT_2026-07-18.md",
    "LIVE_PROBE_FINDINGS.md",
    "audit/AUDIT_PROGRESS.md",
    "audit/DOCUMENT_INVENTORY.md",
    "audit/MASTER_ISSUE_REGISTRY.md",
}

#: Inline escape hatch for a **mixed** document — a current-facing page that
#: narrates the rename itself, or a dated table inside a living document. Put it
#: on the same line as the old name:
#:
#:     `core/confidence_gate.py` is now `core/evidence_support.py` <!-- historical-ref -->
#:
#: One line, one declaration. A whole document is never exempted this way, which
#: is the point: the author has to mark each historical sentence, so a *new*
#: present-tense claim about a removed module still fails the guard.
_HISTORICAL_REF_MARKER = "<!-- historical-ref"

#: Classification of one occurrence of a renamed path.
RENAMED_OLD_PATH_EXISTS = "old_path_exists"          # the rename was undone
RENAMED_HISTORICAL = "historical_provenance"         # declared, allowed
RENAMED_LIVE_REFERENCE = "live_reference"            # DRIFT: reads as current
RENAMED_REPLACEMENT_MISSING = "replacement_missing"  # DRIFT: map is stale


def _renamed_ref_pattern(renames: Mapping[str, str] | None = None) -> re.Pattern[str]:
    """Match every declared old path in both spellings.

    Prose writes `core/confidence_gate`; only code-ish references carry `.py`.
    The old `_PATH_RE` required the extension, so the extensionless form — the
    one a source-of-truth document actually uses — was invisible to this guard.

    ``renames`` defaults to `None` rather than to the module table itself: a
    mutable default is bound once at definition time, so a caller amending the
    table would silently not be seen here.
    """
    renames = _RENAMED_PATHS if renames is None else renames
    stems = sorted(
        {p[:-3] if p.endswith(".py") else p for p in renames},
        key=len,
        reverse=True,
    )
    if not stems:
        return re.compile(r"(?!x)x")  # matches nothing
    return re.compile(
        r"(?<![\w/.\-])(" + "|".join(re.escape(s) for s in stems) + r")(\.py)?(?![\w/\-])"
    )


_RENAMED_REF_RE = _renamed_ref_pattern()


def classify_renamed_reference(
    old_path: str,
    *,
    doc: str,
    line: str,
    exists: Callable[[str], bool],
    renames: Mapping[str, str] | None = None,
    historical_docs: Collection[str] | None = None,
) -> str:
    """Decide what one occurrence of a renamed path is. Pure.

    ``old_path`` is normalised to the key in ``renames`` (with `.py`), ``doc`` is
    the path relative to the docs root, ``exists`` answers "is this repo-relative
    path a file". No I/O of its own, so the whole rule is testable without a
    documentation tree.

    Both tables default to `None` and are resolved per call, so amending the
    module-level table is seen here — a mutable default would have frozen the
    object this function consults at definition time.

    Order matters: a stale rename map is reported even in a document that is
    allowed to keep the old name, because "declared renamed to something that
    does not exist" is a broken declaration, not provenance.
    """
    renames = _RENAMED_PATHS if renames is None else renames
    historical_docs = (
        _HISTORICAL_RENAME_DOCS if historical_docs is None else historical_docs
    )
    if exists(old_path):
        return RENAMED_OLD_PATH_EXISTS
    replacement = renames.get(old_path)
    if replacement is None or not exists(replacement):
        return RENAMED_REPLACEMENT_MISSING
    if doc in historical_docs or _HISTORICAL_REF_MARKER in line:
        return RENAMED_HISTORICAL
    return RENAMED_LIVE_REFERENCE


def _registry_commands() -> set[str]:
    # Since the #278 split the command table lives in two spec volumes that
    # `cli/command_registry.py` combines; scan all three so a token defined in
    # a volume is not reported as an unknown command.
    tokens: set[str] = set()
    for name in ("command_registry.py", "command_specs.py", "command_specs_ops.py"):
        path = REPO / "cli" / name
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        tokens |= set(re.findall(r'canonical="(:[a-z0-9-]+)"', src))
        tokens |= set(re.findall(r'"(:[a-z0-9-]+)"', src))
    return tokens


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    docs_root = DOCS
    if "--docs" in args:
        at = args.index("--docs") + 1
        if at >= len(args):
            print("usage: docs_code_conformance.py [--docs DIR]")
            return 2
        docs_root = Path(args[at]).resolve()

    def _exists(rel: str) -> bool:
        return (REPO / rel).is_file()

    commands = _registry_commands()
    line_counts: dict[Path, int] = {}
    missing_paths: list[str] = []
    stale_anchors: list[str] = []
    live_renamed: list[str] = []
    historical_anchors = 0
    planned_paths = 0
    valid_paths = 0
    renamed_refs = 0
    historical_renamed = 0
    unknown_commands: list[str] = []
    checked_paths = checked_anchors = checked_commands = docs_scanned = 0

    # Root-level markdown was invisible to this guard (the scan covered
    # docs/ only), so FABLE_AUDIT's line anchors drifted silently while the
    # guard reported success — found by the 2026-08 documentation audit.
    # Root files are named with a leading "../" relative to the docs root so
    # the allowlists can address them unambiguously.
    scan_targets = [
        (doc, doc.relative_to(docs_root).as_posix())
        for doc in sorted(docs_root.rglob("*.md"))
    ]
    if docs_root == DOCS:
        scan_targets += [
            (doc, f"../{doc.name}")
            for doc in sorted(REPO.glob("*.md"))
        ]
    for doc, rel_doc in scan_targets:
        docs_scanned += 1
        historical = rel_doc in _HISTORICAL_ANCHOR_DOCS
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            # Renamed paths first, and in both spellings. Owned entirely by this
            # pass so the two spellings cannot be judged by two different rules.
            for match in _RENAMED_REF_RE.finditer(line):
                old_path = match.group(1) + ".py"
                verdict = classify_renamed_reference(
                    old_path, doc=rel_doc, line=line, exists=_exists
                )
                if verdict == RENAMED_OLD_PATH_EXISTS:
                    continue  # the rename was undone; _PATH_RE handles it
                renamed_refs += 1
                if verdict == RENAMED_HISTORICAL:
                    historical_renamed += 1
                elif verdict == RENAMED_REPLACEMENT_MISSING:
                    missing_paths.append(
                        f"{rel_doc}:{lineno}  {match.group(0)} "
                        f"(declared renamed to "
                        f"{_RENAMED_PATHS.get(old_path, '<not in the rename map>')}, "
                        f"which does not exist either)"
                    )
                else:
                    live_renamed.append(
                        f"{rel_doc}:{lineno}  {match.group(0)} "
                        f"-> now {_RENAMED_PATHS[old_path]}"
                    )

            for match in _PATH_RE.finditer(line):
                path_text, anchor = match.group(1), match.group(2)
                target = REPO / path_text
                if path_text in _RENAMED_PATHS and not target.is_file():
                    continue  # counted and judged by the renamed pass above
                checked_paths += 1
                if not target.is_file():
                    lowered = line.lower()
                    if any(marker in lowered for marker in _PLANNED_MARKERS):
                        planned_paths += 1   # documented as not existing yet
                    else:
                        missing_paths.append(f"{rel_doc}:{lineno}  {path_text}")
                    continue
                valid_paths += 1
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

    genuinely_missing = [m for m in missing_paths if "declared renamed" not in m]
    stale_renames = len(missing_paths) - len(genuinely_missing)

    print("Docs <-> code conformance check (read-only)")
    print(f"  documents scanned      : {docs_scanned}")
    print(f"  code paths referenced  : {checked_paths}  "
          f"(valid: {valid_paths}, missing: {len(genuinely_missing)}, "
          f"declared not-yet-written: {planned_paths})")
    print(f"  renamed-path refs      : {renamed_refs}  "
          f"(declared historical: {historical_renamed}, "
          f"live references: {len(live_renamed)}, "
          f"replacement missing: {stale_renames})")
    print(f"  line anchors checked   : {checked_anchors}  "
          f"(out of range: {len(stale_anchors)}, declared historical: {historical_anchors})")
    print(f"  :command tokens        : {checked_commands}  (unknown: {len(set(unknown_commands))})")

    failed = False
    if missing_paths:
        failed = True
        print("\n  MISSING PATHS — the document points at a file that does not exist:")
        for item in missing_paths:
            print(f"    {item}")
    if live_renamed:
        failed = True
        print("\n  RENAMED MODULE PRESENTED AS CURRENT — the module no longer exists;")
        print("  fix the sentence, or declare the line historical with"
              f" '{_HISTORICAL_REF_MARKER} -->':")
        for item in live_renamed:
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
