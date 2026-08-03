"""Archive persistent-memory rows today's knowledge gate would never write.

Root cause A of FABLE_AUDIT.md: the knowledge write gate historically trusted
source *type* weakness only, so three pollution classes entered
``data/persistent_memory.jsonl`` as durable Confidence-0.85 "facts":

  1. rows from NON-ASSERTING sources — log / test_result / memory /
     tool_output / code_repository (MIR-054: observations of one moment,
     not standing claims);
  2. ``file`` rows extracted from CODE files (``tests/*.py`` asserts and the
     like — programs do not assert);
  3. ``file`` rows whose content is a raw code fragment or mojibake, banked
     before ``ClaimExtractor._accept_sentence`` learned to reject them
     (commit e1232e0 and the cluster-1b hardening).

The gate is fixed (PR #200 + this branch), which stops the inflow. This
script drains the pool: it ARCHIVES matching rows — moves them to the
``.archive.jsonl`` store the hot path never injects — it deletes nothing.

**How it decides.** Every decision is delegated to the writer's own
present-day rules, imported from ``core.knowledge_pipeline`` — the same
functions the gate itself calls. There is no second classifier to drift out
of sync (the report and the migration use the identical decision), and rows
this writer never produced — no ``fact``/``knowledge``/``source-backed``
tag triple, or any owner other than ``self`` — are not touched at all:
lessons, user notes and anything hand-written stay where they are, fail
closed.

**How it writes.** Rows are integrity-envelope encoded, so hand-editing the
file corrupts it. The rewrite goes through ``state_file_lock`` +
``rewrite_state_jsonl_unlocked`` / ``append_state_jsonl_unlocked`` — the same
path ``PersistentMemoryStore.archive_record`` uses — and the only mutation a
payload receives is ``archived: true``. The lock is held across read and
write; both stores are only written after both new contents are computed.
The write order is archive-first, and the append is recovery-idempotent: a
retry after a crash between the two writes skips rows the archive already
holds verbatim, and aborts before any write when the archive holds the same
ID with different content.

Dry-run is the default. ``--apply`` writes, after taking a timestamped backup
of BOTH files. A second run finds nothing left to archive (idempotent).

    python scripts/migrate_memory_pollution.py             # show the plan
    python scripts/migrate_memory_pollution.py --apply     # write it
    python scripts/migrate_memory_pollution.py --workspace <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.knowledge_pipeline import (
    _NON_ASSERTING_SOURCE_TYPES,
    _is_broken_encoding,
    _is_code_locator,
    _looks_like_code_fragment,
)
from core.state_integrity import (
    append_state_jsonl_unlocked,
    backup_state_file,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)

# The exact writer signature KnowledgeWritePolicy leaves on every row it
# writes: the tag triple from ``memory_tags`` AND ``owner="self"`` — the
# pipeline calls ``remember(content, tags, "agent-auto", "semantic", "self")``.
# Requiring all of it IS the writer signature (MIR-057): a row without them
# was written by someone else and is not ours to reclassify.
_WRITER_SIGNATURE = frozenset({"fact", "knowledge", "source-backed"})
_WRITER_OWNER = "self"

# memory_tags appends exactly one source-type tag after the signature.
_SOURCE_TYPE_TAGS = _NON_ASSERTING_SOURCE_TYPES | {
    "file", "documentation", "official_site", "web_page", "article",
    "book", "pdf", "video", "podcast", "forum", "user", "unknown",
}


def _source_line(content: str) -> tuple[str, str] | None:
    """Parse the ``Source: <type>:<locator>`` line memory_content embeds.

    Returns (type, locator) or None when the line is absent/malformed —
    absence means the row did not come through ``memory_content`` and the
    migration must not touch it (fail closed).
    """
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
    """Why this row would not pass today's writer — or None to keep it.

    Decision order mirrors ``KnowledgeWritePolicy.decide`` and
    ``ClaimExtractor._accept_sentence``; every predicate is the writer's own,
    imported, not re-implemented. Rows without the writer's tag signature or
    without a parseable Source line are KEPT (fail closed): this migration
    reclassifies only what the knowledge writer provably produced.
    """
    tags = payload.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return None
    tag_set = frozenset(tags)
    if not _WRITER_SIGNATURE <= tag_set:
        return None
    if payload.get("owner") != _WRITER_OWNER:
        # A user- or session-owned row wearing the writer's tags was written
        # by someone else (or edited). Archiving a user's own note would be
        # data loss; leave it alone.
        return None
    if payload.get("archived") is True:
        return None

    parsed = _source_line(str(payload.get("content", "")))
    if parsed is None:
        return None
    source_type, locator = parsed
    # The tag the writer stamped and the Source line it embedded must agree;
    # a mismatch means the row was edited or corrupted — leave it alone.
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
    """Move planned rows to the archive store. Caller holds the ACTIVE lock.

    The archive has its own lockfile: ``state_file_lock(path)`` locks only
    ``<path>.lock``, so holding the active store's lock says nothing about
    the archive. `PersistentMemoryStore.archive_record` appends under
    ``state_file_lock(archive_path)``; this function takes the same lock
    around everything that reads or writes the archive, so the two can never
    interleave an append.

    Recovery-idempotent: the write order is archive-first, so a crash
    between the append and the active-store rewrite leaves the moved rows in
    BOTH stores. A retry therefore checks the archive before appending —
    a row already archived with identical content is not appended again
    (only its active copy is removed); the same ID with DIFFERENT content
    aborts before any backup or write, because silently preferring either
    copy would be a guess.
    """
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

    with state_file_lock(archive_path):
        # Inspect the archive under ITS lock BEFORE mutating anything.
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
                # An interrupted earlier run already archived this exact row;
                # skip the append, the rewrite below removes the active copy.
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
        # Archive first: if the append fails, the active store is untouched
        # and a re-run plans the same rows again. The reverse order could
        # lose rows.
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

    # Lock held across read and write: a cycle writing a record midway would
    # otherwise be dropped by the rewrite.
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
