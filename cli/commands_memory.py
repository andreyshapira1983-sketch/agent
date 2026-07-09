"""Memory / hygiene / rollback REPL commands.

Split out of ``main.py``. Every function here drives the agent only through its
public methods (``rollback``, ``expire_persistent``, ``dedupe_persistent``,
``cleanup_backups``, ``summarise_persistent``, ``archive_persistent``,
``list_persistent``, ``smart_memory_summary`` and the smart-memory stores) plus
``core`` helpers — never back into ``main`` — so there is no import cycle.
``main.py`` re-exports every name below for the REPL dispatch and the tests.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.smart_memory import consolidate_memory

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop


def _handle_rollback(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """`:rollback` — apply / inspect compensation plans."""
    tokens = rest.split()
    if tokens and tokens[0].lower() == "list":
        if not agent.compensation_log:
            print("(no compensation plans registered)", file=sys.stderr)
            return True
        print(f"=== compensation log ({len(agent.compensation_log)} plan(s), oldest first) ===", file=sys.stderr)
        for p in agent.compensation_log:
            preview = ", ".join(
                f"{a.kind}:{a.path or a.backup_path or '-'}" for a in p.actions
            )
            print(f"  {p.id}  tool={p.tool_name}  actions=[{preview}]  — {p.description}", file=sys.stderr)
        return True

    plan_id: str | None = tokens[0] if tokens else None
    report = agent.rollback(plan_id=plan_id, workspace_root=workspace)
    summary = report.summary()
    skipped = summary.get("skipped_reason")
    if skipped:
        print(f"rollback skipped: {skipped}", file=sys.stderr)
        return True
    ok = summary["ok"]
    nop = summary["noop"]
    err = summary["error"]
    print(
        f"rollback {report.plan_id}: ok={ok}, noop={nop}, error={err}",
        file=sys.stderr,
    )
    for o in summary["outcomes"]:
        print(
            f"  [{o['status']}] {o['kind']} {o.get('path') or o.get('backup_path') or ''} "
            f"— {o['detail']}",
            file=sys.stderr,
        )
    return True


def _handle_hygiene(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Dispatch `:hygiene [subcmd] [args...] [--dry-run]`."""
    tokens = rest.split()
    dry_run = False
    if "--dry-run" in tokens:
        dry_run = True
        tokens = [t for t in tokens if t != "--dry-run"]

    sub = (tokens[0].lower() if tokens else "")
    rest_args = tokens[1:]

    if sub in {"", "all"}:
        # Default order: expire (free TTL records) -> dedupe (collapse
        # what's left) -> backups (filesystem). Summarise is omitted
        # from the bulk run because it needs an explicit tag.
        exp = agent.expire_persistent(dry_run=dry_run)
        dup = agent.dedupe_persistent(dry_run=dry_run)
        bak = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"hygiene (dry_run={dry_run}):\n"
            f"  expire   : {len(exp.expired)} record(s) past TTL\n"
            f"  dedupe   : {len(dup.deleted)} near-duplicate(s) collapsed "
            f"({len(dup.groups)} group(s))\n"
            f"  backups  : {len(bak.deleted)} old .bak.<ts> file(s) removed "
            f"(scanned {bak.scanned})",
            file=sys.stderr,
        )
        return True

    if sub == "expire":
        rep = agent.expire_persistent(dry_run=dry_run)
        print(
            f"expire (dry_run={dry_run}): {len(rep.expired)} record(s) past TTL "
            f"out of {rep.scanned} scanned. ids={rep.expired}",
            file=sys.stderr,
        )
        return True

    if sub == "dedupe":
        rep = agent.dedupe_persistent(dry_run=dry_run)
        print(
            f"dedupe (dry_run={dry_run}): {len(rep.deleted)} duplicate(s) collapsed "
            f"into {len(rep.groups)} group(s); threshold={rep.threshold}",
            file=sys.stderr,
        )
        for g in rep.groups:
            print(
                f"  canonical={g.canonical_id} dups={g.duplicate_ids}",
                file=sys.stderr,
            )
        return True

    if sub == "backups":
        rep = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"backups (dry_run={dry_run}): scanned {rep.scanned} backup(s), "
            f"deleted {len(rep.deleted)}, kept {len(rep.kept)}; "
            f"keep_last={rep.keep_last}, max_age_days={rep.max_age_days}",
            file=sys.stderr,
        )
        return True

    if sub == "summarise":
        if not rest_args:
            print(
                "Usage: :hygiene summarise <tag> [--dry-run]",
                file=sys.stderr,
            )
            return True
        tag = rest_args[0]
        rep = agent.summarise_persistent(tag=tag, dry_run=dry_run)
        if rep.skipped_reason:
            print(
                f"summarise (dry_run={dry_run}): skipped — {rep.skipped_reason}",
                file=sys.stderr,
            )
        else:
            print(
                f"summarise (dry_run={dry_run}): merged "
                f"{len(rep.summarised_ids)} record(s) tagged '{tag}' "
                f"into {rep.new_record_id}",
                file=sys.stderr,
            )
        return True

    if sub == "archive":
        threshold = None
        min_age_days = None
        for arg in rest_args:
            if arg.startswith("--threshold="):
                threshold = float(arg.split("=", 1)[1])
            elif arg.startswith("--min-age="):
                min_age_days = int(arg.split("=", 1)[1])
        rep = agent.archive_persistent(
            threshold=threshold, min_age_days=min_age_days, dry_run=dry_run
        )
        print(
            f"archive (dry_run={dry_run}): scanned {rep.scanned} record(s), "
            f"archived {len(rep.archived)} low-value record(s). "
            f"threshold={rep.threshold}, min_age_days={rep.min_age_days}",
            file=sys.stderr,
        )
        return True

    print(f"(unknown :hygiene subcommand: '{sub}')", file=sys.stderr)
    return True


def _print_persistent(agent: AgentLoop) -> None:
    records = agent.list_persistent()
    if not records:
        print("(no persistent records)", file=sys.stderr)
        return
    print(f"\n=== persistent memory ({len(records)} records) ===")
    for r in records:
        text = r.content if isinstance(r.content, str) else json.dumps(r.content, ensure_ascii=False)
        if len(text) > 200:
            text = text[:199] + "…"
        tags = ",".join(r.tags) if r.tags else "-"
        print(f"  [{r.id}] type={r.type} tags={tags}")
        print(f"    {text}")


def _handle_smart_memory(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :smart-memory [--json]", file=sys.stderr)
        return True
    payload = agent.smart_memory_summary()
    agent.log.log("smart_memory_status", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    episodic = payload["episodic"]
    procedural = payload["procedural"]
    consolidation = payload["consolidation"]
    print("=== smart memory ===", file=sys.stderr)
    print(
        f"episodic: episodes={episodic['episodes']} "
        f"outcomes={episodic['outcomes']} path={episodic['path']}",
        file=sys.stderr,
    )
    print(
        f"procedural: procedures={procedural['procedures']} "
        f"statuses={procedural['statuses']} path={procedural['path']}",
        file=sys.stderr,
    )
    print(
        f"consolidation: reports={consolidation['reports']} "
        f"path={consolidation['path']}",
        file=sys.stderr,
    )
    last_report = consolidation.get("last_report")
    if last_report:
        notes = "; ".join(last_report.get("notes") or [])
        print(f"last consolidation: {notes}", file=sys.stderr)
    return True


def _handle_memory_consolidate(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :memory-consolidate [--json]", file=sys.stderr)
        return True
    if (
        agent.episodic_store is None
        or agent.procedural_store is None
        or agent.consolidation_store is None
    ):
        print("(smart memory stores are not configured)", file=sys.stderr)
        return True
    report = consolidate_memory(
        episodes=agent.episodic_store.load(),
        procedures=agent.procedural_store.load(),
    )
    agent.consolidation_store.save(report)
    payload = report.to_dict()
    agent.log.log("memory_consolidation", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True
    print("=== memory consolidation ===", file=sys.stderr)
    print(
        f"report={report.id} episodes={report.episode_count} "
        f"procedures={report.procedure_count}",
        file=sys.stderr,
    )
    for note in report.notes:
        print(f"  - {note}", file=sys.stderr)
    return True
