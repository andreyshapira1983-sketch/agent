"""Check that code paths, line anchors and :commands cited in the docs still resolve."""
from __future__ import annotations

import re
import sys
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

#: Documents whose line anchors deliberately point at an older commit (provenance).
_HISTORICAL_ANCHOR_DOCS = {
    "LIVE_PROBE_FINDINGS.md",
    "../knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "../knowledge/doctrine/self-audit-lessons.md",
}

#: Top-level code directories a documented path may start with.
_CODE_ROOTS = ("core", "cli", "app", "api", "tools", "tests", "scripts", "bug_lab",
               "project_intelligence")

_PATH_RE = re.compile(
    r"(?<![\w/.])((?:" + "|".join(_CODE_ROOTS) + r")(?:/[\w.\-]+)+\.py)(?::(\d+))?"
)
#: The `?` in the lookbehind skips quoted regexes such as `(?:new\s+)?` (not a :new command).
_COMMAND_RE = re.compile(r"(?<![\w:?])(:[a-z][a-z0-9-]{2,})(?![\w-])")

#: Command-like tokens that are not registry commands: REPL block tokens (handled
#: before dispatch), prose placeholders, and a documented probe input.
_NON_COMMAND_TOKENS = {
    ":task-begin", ":task-end", ":task-abort", ":end",   # REPL block tokens
    ":command", ":commands", ":token",                    # prose placeholders
    # A deliberately unknown command the docs use as a probe input.
    ":unknown-xyz",
}

#: Words on a line that declare a path as not written yet (planned), so it is not drift.
_PLANNED_MARKERS = ("proposed", "missing test", "planned", "should be added", "to be written")

#: Words declaring a path named because it does not exist (kept apart from "planned").
#: The phrase must be on the same line as the path, so neighbours cannot borrow it.
_NONEXISTENT_MARKERS = (
    "does not exist", "do not exist", "none of these exist",
    "не существует", "не существуют", "выдуман",
)

#: Directories of verbatim run transcripts: paths there are quoted log lines, not claims.
_VERBATIM_TRANSCRIPT_DIRS = {
}

#: Renamed files, old path -> new path. An entry only maps the name; where the old name
#: may still appear is decided by _HISTORICAL_RENAME_DOCS / _HISTORICAL_REF_MARKER.
_RENAMED_PATHS: dict[str, str] = {
    "core/confidence_gate.py": "core/evidence_support.py",
    "tests/test_confidence_gate.py": "tests/test_evidence_support.py",
    "core/loop_methods2.py": "core/loop_memory_read.py",
    "core/loop_methods.py": "core/loop_hygiene.py",
    "cli/commands_misc.py": "cli/commands_audit.py",
    "core/loop_helpers.py": "core/answer_format.py",
    "core/hygiene.py": "core/memory_hygiene.py",
    "tests/test_commands_misc.py": "tests/test_cli_operator_commands.py",
}

#: Documents where old module names are provenance by nature (dated audits, findings).
_HISTORICAL_RENAME_DOCS = {
    "LIVE_PROBE_FINDINGS.md",
    "../knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "../knowledge/doctrine/self-audit-lessons.md",
}

#: Same-line marker that declares one line of a living document historical, e.g.
#:     `core/confidence_gate.py` is now `core/evidence_support.py` <!-- historical-ref -->
_HISTORICAL_REF_MARKER = "<!-- historical-ref"

#: Classification of one occurrence of a renamed path.
RENAMED_OLD_PATH_EXISTS = "old_path_exists"          # the rename was undone
RENAMED_HISTORICAL = "historical_provenance"         # declared, allowed
RENAMED_LIVE_REFERENCE = "live_reference"            # DRIFT: reads as current
RENAMED_REPLACEMENT_MISSING = "replacement_missing"  # DRIFT: map is stale


def _renamed_ref_pattern(renames: Mapping[str, str] | None = None) -> re.Pattern[str]:
    """Match every declared old path, with or without the .py extension."""
    renames = _RENAMED_PATHS if renames is None else renames
    stems = sorted(
        {p.removesuffix(".py") for p in renames},
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
    """Decide what one occurrence of a renamed path is. Pure."""
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


def _absence_is_declared(line: str) -> bool:
    """True when the line itself declares the path planned or deliberately nonexistent."""
    lowered = line.lower()
    return any(m in lowered for m in _PLANNED_MARKERS) or any(
        m in lowered for m in _NONEXISTENT_MARKERS
    )


def _registry_commands() -> set[str]:
    # The command table is split across these three files; scan them all.
    tokens: set[str] = set()
    for name in ("command_registry.py", "command_specs.py", "command_specs_ops.py"):
        path = REPO / "cli" / name
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        tokens |= set(re.findall(r'canonical="(:[a-z0-9-]+)"', src))
        tokens |= set(re.findall(r'"(:[a-z0-9-]+)"', src))
    return tokens


def _scan_targets(docs_root: Path) -> list[tuple[Path, str]]:
    """(path, allowlist name) pairs to check; a custom --docs root scans only itself."""
    targets = [
        (doc, doc.relative_to(docs_root).as_posix())
        for doc in sorted(docs_root.rglob("*.md"))
    ]
    if docs_root != DOCS:
        return targets
    targets += [(doc, f"../{doc.name}") for doc in sorted(REPO.glob("*.md"))]
    targets += [
        (doc, f"../{doc.relative_to(REPO).as_posix()}")
        for doc in sorted((REPO / "knowledge").rglob("*.md"))
    ]
    return targets


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
    transcript_paths = 0
    valid_paths = 0
    renamed_refs = 0
    historical_renamed = 0
    unknown_commands: list[str] = []
    checked_paths = checked_anchors = checked_commands = docs_scanned = 0

    # Root and knowledge/ files are named "../<path>" so the allowlists can address them.
    scan_targets = _scan_targets(docs_root)
    for doc, rel_doc in scan_targets:
        docs_scanned += 1
        historical = rel_doc in _HISTORICAL_ANCHOR_DOCS
        transcript = any(rel_doc.startswith(d) for d in _VERBATIM_TRANSCRIPT_DIRS)
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            # Renamed paths first, both spellings, all judged by this one pass.
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
                    if _absence_is_declared(line):
                        planned_paths += 1   # documented as not existing (yet)
                    elif transcript:
                        transcript_paths += 1  # quoted from a run log, not claimed
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
                        if historical or _HISTORICAL_REF_MARKER in line:
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
          f"declared not-yet-written: {planned_paths}, "
          f"quoted in a transcript: {transcript_paths})")
    print(f"  renamed-path refs      : {renamed_refs}  "
          f"(declared historical: {historical_renamed}, "
          f"live references: {len(live_renamed)}, "
          f"replacement missing: {stale_renames})")
    print(f"  line anchors checked   : {checked_anchors}  "
          f"(out of range: {len(stale_anchors)}, declared historical: {historical_anchors})")
    print(f"  :command tokens        : {checked_commands}  (unknown: {len(set(unknown_commands))})")

    failed = _print_findings(missing_paths, live_renamed, stale_anchors, unknown_commands)
    print("\n  RESULT:", "DRIFT FOUND" if failed else "every code reference resolves.")
    return 1 if failed else 0


def _print_findings(
    missing_paths: list[str],
    live_renamed: list[str],
    stale_anchors: list[str],
    unknown_commands: list[str],
) -> bool:
    """Print each non-empty finding block; True when anything was printed."""
    sections: list[tuple[list[str], list[str]]] = [
        (missing_paths,
         ["\n  MISSING PATHS — the document points at a file that does not exist:"]),
        (live_renamed,
         ["\n  RENAMED MODULE PRESENTED AS CURRENT — the module no longer exists;",
          ("  fix the sentence, or declare the line historical with"
           f" '{_HISTORICAL_REF_MARKER} -->':")]),
        (stale_anchors,
         ["\n  STALE LINE ANCHORS in documents not declared historical:"]),
        (sorted(set(unknown_commands)),
         ["\n  UNKNOWN COMMANDS — documented but not in the registry:"]),
    ]
    failed = False
    for items, heading in sections:
        if not items:
            continue
        failed = True
        for line in heading:
            print(line)
        for item in items:
            print(f"    {item}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
