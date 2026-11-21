"""Memory, hygiene, rollback and receipt REPL commands; they never import main (no cycle)."""
from __future__ import annotations

import json
import math
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


def _parse_archive_args(args: list[str]) -> tuple[float | None, int | None]:
    """Read `--threshold=<number>` and `--min-age=<days>`; ValueError on a bad number or word."""
    threshold = None
    min_age_days = None
    for arg in args:
        if arg.startswith("--threshold="):
            threshold = float(arg.split("=", 1)[1])
            if not (math.isfinite(threshold) and 0 < threshold <= 1):
                raise ValueError("threshold must be a finite number in (0, 1]")
        elif arg.startswith("--min-age="):
            min_age_days = int(arg.split("=", 1)[1])
        else:
            raise ValueError(f"unknown archive argument {arg!r}")
    return threshold, min_age_days


#: `:hygiene` mode flags; without --apply/--yes every pass is a dry run.
_HYGIENE_MODE_FLAGS = frozenset({"--dry-run", "--apply", "--yes"})
_HYGIENE_NO_ARG_SUBS = frozenset({"", "all", "expire", "dedupe", "backups", "episodic"})
_HYGIENE_DRY_HINT = (
    "(сухой прогон: ничего не изменено; чтобы выполнить по-настоящему, "
    "повторите команду с --apply)"
)


def _hygiene_done(dry_run: bool) -> bool:
    if dry_run:
        print(_HYGIENE_DRY_HINT, file=sys.stderr)
    return True


def _hygiene_refuse(sub: str, words: list[str]) -> bool:
    print(
        f"(:hygiene {sub or 'all'}: unknown argument(s) {' '.join(words)} — nothing done; "
        "mode flags: --dry-run (default) or --apply)",
        file=sys.stderr,
    )
    return True


def _handle_hygiene(rest: str, agent: AgentLoop, workspace: Path) -> bool:  # noqa: PLR0911 — flat: one refusal per bad flag, each a guard clause
    """Dispatch `:hygiene [subcmd] [args...] [--dry-run | --apply]`; dry run unless --apply/--yes."""
    tokens = rest.split()
    flags = {t for t in tokens if t in _HYGIENE_MODE_FLAGS}
    tokens = [t for t in tokens if t not in _HYGIENE_MODE_FLAGS]
    apply = bool(flags & {"--apply", "--yes"})
    if apply and "--dry-run" in flags:
        print("(:hygiene: --dry-run and --apply together — nothing done)", file=sys.stderr)
        return True
    dry_run = not apply
    # Doubles without the method get no brake; loop facades do not delete anyway.
    brake_of = getattr(agent, "_hygiene_apply_brake", None)
    brake = brake_of() if apply and callable(brake_of) else None
    if brake:
        print(f"(:hygiene --apply refused: {brake} — nothing deleted; "
              "a dry-run report is still allowed)", file=sys.stderr)
        return True

    sub = (tokens[0].lower() if tokens else "")
    rest_args = tokens[1:]
    if sub.startswith("-"):
        return _hygiene_refuse("", tokens)
    if sub in _HYGIENE_NO_ARG_SUBS and rest_args:
        return _hygiene_refuse(sub, rest_args)

    if sub in {"", "all"}:
        # Expire TTL records before dedupe; summarise needs a tag, so it is not in the bulk run.
        exp = agent.expire_persistent(dry_run=dry_run)
        dup = agent.dedupe_persistent(dry_run=dry_run)
        epi = agent.prune_episodic(dry_run=dry_run)
        edu = agent.dedupe_episodic(dry_run=dry_run)
        bak = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"hygiene (dry_run={dry_run}):\n"
            f"  expire   : {len(exp.expired)} record(s) past TTL\n"
            f"  dedupe   : {len(dup.deleted)} near-duplicate(s) collapsed "
            f"({len(dup.groups)} group(s))\n"
            f"  episodic : {len(epi)} stale episode(s) pruned, "
            f"{len(edu)} repeat(s) collapsed\n"
            f"  backups  : {len(bak.deleted)} old .bak.<ts> file(s) removed "
            f"(scanned {bak.scanned})",
            file=sys.stderr,
        )
        return _hygiene_done(dry_run)

    if sub == "expire":
        rep = agent.expire_persistent(dry_run=dry_run)
        print(
            f"expire (dry_run={dry_run}): {len(rep.expired)} record(s) past TTL "
            f"out of {rep.scanned} scanned. ids={rep.expired}",
            file=sys.stderr,
        )
        return _hygiene_done(dry_run)

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
        return _hygiene_done(dry_run)

    if sub == "backups":
        rep = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"backups (dry_run={dry_run}): scanned {rep.scanned} backup(s), "
            f"deleted {len(rep.deleted)}, kept {len(rep.kept)}; "
            f"keep_last={rep.keep_last}, max_age_days={rep.max_age_days}",
            file=sys.stderr,
        )
        return _hygiene_done(dry_run)

    if sub == "episodic":
        pruned = agent.prune_episodic(dry_run=dry_run)
        print(
            f"episodic (dry_run={dry_run}): {len(pruned)} stale episode(s) "
            f"pruned. ids={pruned}",
            file=sys.stderr,
        )
        return _hygiene_done(dry_run)

    if sub == "summarise":
        if not rest_args:
            print(
                "Usage: :hygiene summarise <tag> [--dry-run | --apply]",
                file=sys.stderr,
            )
            return True
        if len(rest_args) > 1 or rest_args[0].startswith("-"):
            return _hygiene_refuse(sub, rest_args)
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
        return _hygiene_done(dry_run)

    if sub == "archive":
        try:
            threshold, min_age_days = _parse_archive_args(rest_args)
        except ValueError:
            print(
                "Usage: :hygiene archive [--threshold=<number>] [--min-age=<days>] [--dry-run]",
                file=sys.stderr,
            )
            return True
        rep = agent.archive_persistent(
            threshold=threshold, min_age_days=min_age_days, dry_run=dry_run
        )
        print(
            f"archive (dry_run={dry_run}): scanned {rep.scanned} record(s), "
            f"archived {len(rep.archived)} low-value record(s). "
            f"threshold={rep.threshold}, min_age_days={rep.min_age_days}",
            file=sys.stderr,
        )
        # Name where the records went and how to read them back.
        if not dry_run and rep.archived:
            print(
                "  they now live in data/persistent_memory.archive.jsonl — "
                "читается `:smart-memory archive <слово>` (MIR-138); "
                "пробуждение в активную память — отдельная власть (MIR-156)",
                file=sys.stderr,
            )
        return _hygiene_done(dry_run)

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
    # `archive <word>` searches archived records read-only; waking them is a separate power.
    if tokens and tokens[0] == "archive":
        term = " ".join(tokens[1:]).strip()
        if not term:
            print("Usage: :smart-memory archive <слово>", file=sys.stderr)
            return True
        store = getattr(agent, "persistent_store", None)
        if store is None:
            print("(постоянная память не подключена)", file=sys.stderr)
            return True
        hits = store.search_archive(term)
        agent.log.log("memory_archive_read", {"term": term, "hits": len(hits)})
        print(f"архив: найдено {len(hits)} по '{term}'", file=sys.stderr)
        for rec in hits:
            print(f"  [{str(rec.created_at)[:19]}] {rec.id} "
                  f"{str(rec.content)[:90]}", file=sys.stderr)
        return True
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :smart-memory [archive <слово>] [--json]", file=sys.stderr)
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
    ):
        print("(smart memory stores are not configured)", file=sys.stderr)
        return True
    # Computed fresh and never persisted: it only tallies statuses the procedures hold.
    report = consolidate_memory(
        episodes=agent.episodic_store.load(),
        procedures=agent.procedural_store.load(),
    )
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


def _handle_receipts(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """`:receipts [trace <part>] [N]`: show the accumulated tool receipts."""
    from core.tool_receipts import ToolReceiptLedger, default_receipts_path, summarise_receipts

    tokens = rest.split()
    trace = None
    recent = 10
    i = 0
    while i < len(tokens):
        if tokens[i] == "trace" and i + 1 < len(tokens):
            trace = tokens[i + 1]
            i += 2
            continue
        try:
            recent = max(0, int(tokens[i]))
        except ValueError:
            print("Usage: :receipts [trace <кусок>] [N]", file=sys.stderr)
            return True
        i += 1
    ledger = ToolReceiptLedger(path=default_receipts_path(workspace))
    report = summarise_receipts(ledger, trace=trace, recent=recent)
    agent.log.log("receipts_read", {"trace": trace or "", "recent": recent})
    print(report, file=sys.stderr)
    return True
