"""Archive persistent-memory rows that today's knowledge write gate would never write.

Every decision reuses the writer's own present-day rules from core.knowledge_pipeline,
and rows the knowledge writer did not produce are never touched. Matching rows are moved
(archived: true) to persistent_memory.archive.jsonl, never deleted. Dry-run by default;
--apply writes after backing up both files. A second run finds nothing (idempotent).

Usage: python scripts/migrate_memory_pollution.py [--apply] [--json] [--workspace PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.knowledge_pipeline import (  # noqa: E402
    _NON_ASSERTING_SOURCE_TYPES,
    _is_broken_encoding,
    _is_code_locator,
    _looks_like_code_fragment,
)
from core.state_integrity import (  # noqa: E402
    append_state_jsonl_unlocked,
    backup_state_file,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)

# The knowledge writer's signature: this tag triple and owner "self". A row without
# it was written by someone else and is not ours to reclassify.
_WRITER_SIGNATURE = frozenset({"fact", "knowledge", "source-backed"})
_WRITER_OWNER = "self"

# memory_tags appends exactly one source-type tag after the signature.
_SOURCE_TYPE_TAGS = _NON_ASSERTING_SOURCE_TYPES | {
    "file", "documentation", "official_site", "web_page", "article",
    "book", "pdf", "video", "podcast", "forum", "user", "unknown",
}


def _source_line(content: str) -> tuple[str, str] | None:
    """Parse the ``Source: <type>:<locator>`` line; None (keep the row) when absent or malformed."""
    for line in str(content).splitlines():
        line = line.strip()
        if line.startswith("Source: "):
            rest = line[len("Source: "):]
            source_type, sep, locator = rest.partition(":")
            if sep and source_type:
                return source_type.strip(), locator.strip()
            return None
    return None


def _claim_text(content: str) -> str:
    """The claim is everything before the ``Source:`` line memory_content added."""
    lines: list[str] = []
    for line in str(content).splitlines():
        if line.strip().startswith("Source: "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def archive_reason(payload: dict[str, Any]) -> str | None:
    """Why today's writer would reject this row, or None to keep it (unknown rows fail closed)."""
    tags = payload.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return None
    tag_set = frozenset(tags)
    if not tag_set >= _WRITER_SIGNATURE:
        return None
    if payload.get("owner") != _WRITER_OWNER:
        # A user- or session-owned row wearing the writer's tags is someone else's note.
        return None
    if payload.get("archived") is True:
        return None

    parsed = _source_line(str(payload.get("content", "")))
    if parsed is None:
        return None
    source_type, locator = parsed
    # The stamped tag and the embedded Source line must agree; a mismatch means an edited row.
    if source_type not in tag_set or source_type not in _SOURCE_TYPE_TAGS:
        return None

    if source_type in _NON_ASSERTING_SOURCE_TYPES:
        return f"non-asserting source type: {source_type}"
    if source_type == "file" and _is_code_locator(locator):
        return f"code file: {locator}"

    claim = _claim_text(str(payload.get("content", "")))
    if not claim:
        return None
    if _is_broken_encoding(claim):
        return "broken encoding (mojibake) in claim text"
    if _looks_like_code_fragment(claim):
        return "claim text is a code fragment"
    return None


def plan_migration(payloads: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """Pure: which rows would be archived, and why. No I/O, no mutation."""
    plan: list[tuple[int, str]] = []
    for index, payload in enumerate(payloads):
        reason = archive_reason(payload)
        if reason:
            plan.append((index, reason))
    return plan


def describe(payloads: list[dict[str, Any]], plan: list[tuple[int, str]]) -> str:
    out: list[str] = []
    out.append(f"active rows: {len(payloads)}   would archive: {len(plan)}")

    by_reason: dict[str, int] = {}
    for _index, reason in plan:
        key = reason.split(":")[0]
        by_reason[key] = by_reason.get(key, 0) + 1
    for key, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        out.append(f"    {count:4}  {key}")

    if plan:
        out.append("\nSAMPLE (first 10)")
        for index, reason in plan[:10]:
            row = payloads[index]
            claim = _claim_text(str(row.get("content", "")))[:70].replace("\n", " ")
            out.append(f"    {row.get('id', '<no id>')}  [{reason}]  {claim!r}")

    kept = len(payloads) - len(plan)
    out.append(f"\nKEPT (fail closed or genuinely prose-backed): {kept}")
    return "\n".join(out)


def apply_migration(store_path: Path, payloads: list[dict[str, Any]],
                    plan: list[tuple[int, str]]) -> dict[str, Any]:
    """Move planned rows to the archive store, archive first; the caller holds the active lock."""
    archive_path = store_path.with_suffix(".archive.jsonl")
    planned = {index for index, _reason in plan}

    keep: list[dict[str, Any]] = []
    move: list[dict[str, Any]] = []
    for index, payload in enumerate(payloads):
        if index in planned:
            moved = dict(payload)
            moved["archived"] = True
            move.append(moved)
        else:
            keep.append(payload)

    # The active store's lock does not cover the archive, which has its own lockfile.
    with state_file_lock(archive_path):
        # Read the archive before any write: a conflicting id aborts with both stores intact.
        existing_by_id: dict[str, dict[str, Any]] = {}
        if archive_path.exists():
            for row in read_state_jsonl_unlocked(archive_path):
                row_id = row.get("id")
                if isinstance(row_id, str) and row_id:
                    existing_by_id[row_id] = row
        to_append: list[dict[str, Any]] = []
        recovered = 0
        for moved in move:
            row_id = moved.get("id")
            prior = existing_by_id.get(row_id) if isinstance(row_id, str) else None
            if prior is None:
                to_append.append(moved)
            elif prior == moved:
                # An interrupted run already archived this row; the rewrite drops its active copy.
                recovered += 1
            else:
                raise ValueError(
                    f"archive already holds id {row_id!r} with different content; "
                    f"refusing to touch either store — resolve the conflict "
                    f"manually before re-running"
                )

        backup_active = backup_state_file(store_path)
        backup_archive = (
            backup_state_file(archive_path) if archive_path.exists() else None
        )
        # Archive first: a failed append leaves the active store intact; reversing could lose rows.
        if to_append:
            append_state_jsonl_unlocked(archive_path, to_append)
    rewrite_state_jsonl_unlocked(store_path, keep)
    return {
        "archived": len(move),
        "appended": len(to_append),
        "recovered_duplicates": recovered,
        "kept": len(keep),
        "backup_active": str(backup_active),
        "backup_archive": str(backup_archive) if backup_archive else None,
        "archive_path": str(archive_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the change (default is a dry run that touches nothing)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the report as JSON",
    )
    args = parser.parse_args(argv)

    path = Path(args.workspace) / "data" / "persistent_memory.jsonl"
    if not path.exists():
        print(f"no persistent store at {path} — nothing to do")
        return 0

    # Hold the lock across read and write, or a record written meanwhile is lost by the rewrite.
    with state_file_lock(path):
        payloads = read_state_jsonl_unlocked(path)
        plan = plan_migration(payloads)

        if args.json:
            report: dict[str, Any] = {
                "store": str(path),
                "active_rows": len(payloads),
                "would_archive": len(plan),
                "reasons": [
                    {"id": payloads[i].get("id"), "reason": r} for i, r in plan
                ],
                "applied": False,
            }
        else:
            print(describe(payloads, plan))

        if not plan:
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print("\nnothing to archive — store unchanged")
            return 0
        if not args.apply:
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print("\ndry run — store unchanged. Re-run with --apply to write.")
            return 0

        result = apply_migration(path, payloads, plan)

    if args.json:
        report["applied"] = True
        report.update(result)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\nbackup (active):  {result['backup_active']}")
        if result["backup_archive"]:
            print(f"backup (archive): {result['backup_archive']}")
        if result["recovered_duplicates"]:
            print(
                f"recovered {result['recovered_duplicates']} row(s) an "
                f"interrupted earlier run had already archived"
            )
        print(
            f"archived {result['archived']} row(s) -> {result['archive_path']}; "
            f"{result['kept']} row(s) kept"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
