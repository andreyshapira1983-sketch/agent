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
    "LIVE_PROBE_FINDINGS.md",
    "../knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "MEMORY_FIX_PLAN.md",
    "../knowledge/doctrine/self-audit-lessons.md",
    "audit/archive/CORE_AUDIT_2026-07-18.md",
    "audit/archive/daemon-progress.md",
}

#: Top-level code directories a documented path may start with.
_CODE_ROOTS = ("core", "cli", "app", "api", "tools", "tests", "scripts", "bug_lab",
               "project_intelligence")

_PATH_RE = re.compile(
    r"(?<![\w/.])((?:" + "|".join(_CODE_ROOTS) + r")(?:/[\w.\-]+)+\.py)(?::(\d+))?"
)
#: `(?<![\w:?])` — the `?` keeps `(?:new\s+)?` out: a regex quoted from
#: `core/injection_guard.py` is not a `:new` command. Found 2026-09-05 when the
#: exam record quoted the `override` pattern verbatim.
_COMMAND_RE = re.compile(r"(?<![\w:?])(:[a-z][a-z0-9-]{2,})(?![\w-])")

#: Tokens that look like commands in prose but are not dispatched commands.
#: The four REPL block tokens are intercepted by the dialogue loop before
#: dispatch and are deliberately absent from the registry — pinned by
#: ``tests/characterization/test_command_surface_snapshot.py`` ("repl_control
#: _tokens": 4). The rest are generic placeholders ("the :command surface").
_NON_COMMAND_TOKENS = {
    ":task-begin", ":task-end", ":task-abort", ":end",   # REPL block tokens
    ":command", ":commands", ":token",                    # prose placeholders
    # The pre-model census (PROJECT_MAP, 2026-08-09) documents a DELIBERATELY
    # unknown command as a probe input: mechanism M8 is "what the dispatcher
    # does with a command that does not exist". It is a documented non-command,
    # the same class as the prose placeholders above.
    ":unknown-xyz",
}

#: A path introduced by one of these words is a file the document says does NOT
#: exist yet (proposed/planned test, missing coverage). Referencing it is
#: correct documentation, not drift.
_PLANNED_MARKERS = ("proposed", "missing test", "planned", "should be added", "to be written")

#: Путь, названный ИМЕННО ПОТОМУ, что его не существует. Отдельно от
#: `_PLANNED_MARKERS`: «запланирован» и «выдуман» — разные вещи, и складывать их
#: значило бы записать вымысел в намерение.
#:
#: Категория заведена 2026-08-15: рефлексия сочинила девять имён модулей
#: (`reasoning`, `citation`, `user_contract`…), следующий прогон пошёл их
#: читать, и разбор в docs/CODE_NOTES.md обязан назвать их дословно — иначе
#: запись о вымысле нечитаема. Фраза требуется В ТОЙ ЖЕ СТРОКЕ, что и путь:
#: заявление на весь абзац разрешило бы соседям тихо протащить живую ссылку.
_NONEXISTENT_MARKERS = (
    "does not exist", "do not exist", "none of these exist",
    "не существует", "не существуют", "выдуман",
)

#: Directories of VERBATIM run transcripts — the agent's own log lines, copied
#: unedited beside the exam that produced them. A path there is what the agent
#: typed into a tool call, not a claim by the document's author; the file it
#: names may have been created and undone within the same run (the 2026-09-05
#: exam wrote a 17-byte placeholder test, compensation registered its removal).
#: Editing a log line to carry a marker would falsify the record, so the
#: exemption is by directory and counted separately in the summary. Prose that
#: NARRATES such a run lives outside these directories and is judged as prose.
_VERBATIM_TRANSCRIPT_DIRS = {
    "audit/exam_self_knowledge_2026-09-05/",
}

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
    # measurement showed the old scalar conflated three different situations.
    "core/confidence_gate.py": "core/evidence_support.py",
    "tests/test_confidence_gate.py": "tests/test_evidence_support.py",
    # Dissolved 2026-08-04. `loop_methods2` was never a module: it was the
    # output of `core/incremental_splitter.py`, which cut `core/loop.py` by
    # LINE BUDGET rather than by responsibility, so the name was a sequence
    # number and told a reader nothing. Ten of its eleven methods turned out to
    # be one subject — the loop's memory — and it split in two along the line
    # that matters: reading and writing. The mapping below can only name one
    # successor; the other is `core/loop_memory_write.py`, and the eleventh
    # method (`_interpret`, 6 lines, not memory at all) went to
    # `core/loop_observe.py`, beside its only caller.
    "core/loop_methods2.py": "core/loop_memory_read.py",
    # Dissolved 2026-08-04, same reason and same tool. This one held FIVE
    # unrelated responsibilities, and three of them were never loop code at
    # all: the operator memory commands, repair and hygiene are called by the
    # CLI and by `agent_tick.py`, never by the cycle. Successors:
    # `core/loop_sensor.py`, `core/loop_knowledge.py`,
    # `core/loop_memory_commands.py`, `core/loop_repair.py` and the one named
    # below, which the mapping can point at.
    "core/loop_methods.py": "core/loop_hygiene.py",
    # Dissolved 2026-08-04: a file whose own docstring said "Miscellaneous"
    # and held six unrelated subjects. Successors: `cli/commands_team.py`,
    # `cli/commands_connectors.py`, `cli/commands_learn.py`,
    # `cli/commands_knowledge_review.py` and the one named below.
    "cli/commands_misc.py": "cli/commands_audit.py",
    # Dissolved 2026-08-04: "helpers" named nothing. Almost the whole file was
    # one subject — how the answer is formatted and how citations are written —
    # and it moved to the name below. Four foreign pieces went to their own
    # subjects: `_to_text`/`untrusted_scan_view` to `core/injection_guard.py`,
    # `new_trace_id` to `core/ids.py`, `DEFAULT_MAX_REPLAN_ATTEMPTS` to
    # `core/replan.py`.
    "core/loop_helpers.py": "core/answer_format.py",
    # Split 2026-08-04 by domain: the file said "Memory Hygiene" but one of its
    # policies removed `.bak` FILES for the self-apply lane, not memory records.
    # Backups went to `core/backup_cleanup.py`; the four memory policies kept
    # their subject under the name below.
    "core/hygiene.py": "core/memory_hygiene.py",
    "tests/test_commands_misc.py": "tests/test_cli_operator_commands.py",
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
    "audit/archive/CORE_AUDIT_2026-07-18.md",
    "LIVE_PROBE_FINDINGS.md",
    # Added 2026-08-04 with the `loop_methods2` dissolution: dated audits and
    # fix plans whose job is to say what was true when they were written. The
    # old module name is the CORRECT word there — the finding was made against
    # that file, and rewriting it would falsify the record.
    "MEMORY_FIX_PLAN.md",
    "../knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "audit/archive/Технический_анализ_автономного_агента_и_функций_мозга.md",
    "../knowledge/doctrine/self-audit-lessons.md",
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

    Prose writes `core/confidence_gate`; only code-ish references carry
    `.py`. The old `_PATH_RE` required the extension, so the extensionless
    form — the one a source-of-truth document actually uses — was invisible
    to this guard.
    """
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
    """The line itself says the path is planned, or named BECAUSE it is absent.

    Both are correct documentation of a missing file. Kept as two marker sets
    all the same: «запланирован» и «выдуман» — разные вещи, and the second
    exists so a разбор вымысла can quote the invented name verbatim.
    """
    lowered = line.lower()
    return any(m in lowered for m in _PLANNED_MARKERS) or any(
        m in lowered for m in _NONEXISTENT_MARKERS
    )


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


def _scan_targets(docs_root: Path) -> list[tuple[Path, str]]:
    """Every Markdown this check owns, paired with the name the allowlists use.

    A custom `--docs` root scans only itself, which is what lets the tests
    point the script at an isolated tree.
    """
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

    # Root-level markdown was invisible to this guard (the scan covered
    # docs/ only), so FABLE_AUDIT's line anchors drifted silently while the
    # guard reported success — found by the 2026-08 documentation audit.
    # Root files are named with a leading "../" relative to the docs root so
    # the allowlists can address them unambiguously.
    scan_targets = _scan_targets(docs_root)
    for doc, rel_doc in scan_targets:
        docs_scanned += 1
        historical = rel_doc in _HISTORICAL_ANCHOR_DOCS
        transcript = any(rel_doc.startswith(d) for d in _VERBATIM_TRANSCRIPT_DIRS)
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
