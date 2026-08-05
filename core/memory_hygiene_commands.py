"""Memory hygiene as operator and daemon commands: expire, dedupe, prune, archive.

Census item B1. This lived in `core/loop_hygiene.py`, a mixin composed into
`AgentLoop` — and the cycle never called it. Verified by call sites:
`agent_tick.py` runs the whole pass, `cli/commands_memory.py` runs the
individual steps, and no phase of `_run_inner` touches either. It sat in the
loop layer because the daemon and the CLI reach it through the agent object.

Per the operator's ruling, `agent` stays the single entry point —
`agent.run_maintenance_pass()` still works and no caller changed — while the
mixin stops owning the logic. Dependencies arrive as arguments, never as an
agent to reach into.

One thing deliberately did NOT move: the permission question. Whether a durable
write is allowed at all is the agent's policy (`_durable_learning_suppressed`,
the audit brake, the dry-run brake, the sink allowlist), and answering it here
would put a second copy of that decision in a second place — the defect this
census spent its time finding. The facade answers it and passes the verdict in
as `suppressed_reason`.
"""
from __future__ import annotations

from typing import Any

from core.model_router import ModelRole


def run_maintenance_pass(
    *,
    log: Any,
    persistent_store: Any,
    episodic_store: Any,
    assumption_store: Any,
    suppressed_reason: str | None,
    dry_run: bool = True,
) -> dict:
    """One bounded hygiene pass: expire → dedupe → prune episodes → archive.

    Exists so the unattended agent keeps its own memory: every operation below
    already existed, but the sole caller was the `:hygiene` command, and nobody
    types that on the daemon path (MIR-045).

    `suppressed_reason` is the answer to "may this write at all", decided by the
    caller. `None` means yes; any string is the reason it may not, and is
    reported rather than silently returning an empty pass.

    `dry_run=True` (the default) counts and reports without removing. Thresholds
    tuned on synthetic data should be seen against a real store before they are
    allowed to delete from it.

    `summarise_persistent` is deliberately absent: it needs an LLM call, so it is
    neither free nor deterministic, and it stays an operator action.

    Returns a delta report — the only way an operator learns what ran while
    nobody was watching.
    """
    if suppressed_reason is not None:
        log.log("maintenance_pass_skipped", {"reason": suppressed_reason})
        return {"skipped": suppressed_reason, "dry_run": dry_run}

    report: dict = {"skipped": None, "dry_run": dry_run}
    try:
        expiry = expire_persistent(
            log=log, persistent_store=persistent_store, dry_run=dry_run
        )
        report["expired"] = len(getattr(expiry, "expired", []) or [])

        dedup = dedupe_persistent(
            log=log, persistent_store=persistent_store, dry_run=dry_run
        )
        report["deduped"] = len(getattr(dedup, "deleted", []) or [])

        pruned = prune_episodic(
            log=log, episodic_store=episodic_store, dry_run=dry_run
        )
        report["episodes_pruned"] = len(pruned or [])

        archive = archive_persistent(
            log=log, persistent_store=persistent_store, dry_run=dry_run
        )
        report["archived"] = len(getattr(archive, "archived", []) or [])

        compacted = compact_assumptions(
            log=log, assumption_store=assumption_store, dry_run=dry_run
        )
        report["assumptions_duplicates_removed"] = int(
            compacted.get("duplicates_removed", 0)
        )
        report["assumptions_over_cap_removed"] = int(
            compacted.get("over_cap_removed", 0)
        )
    except Exception as exc:  # noqa: BLE001
        # Maintenance must never take the tick down with it: a corrupt state
        # file is a reason to skip cleanup, not to stop working. Reported, not
        # swallowed — the report carries the type back to the caller.
        log.log(
            "maintenance_pass_error",
            {"error": type(exc).__name__, "detail": str(exc)[:200]},
        )
        report["error"] = type(exc).__name__

    log.log("maintenance_pass", report)
    _explain(log=log, report=report, persistent_store=persistent_store, dry_run=dry_run)
    return report


def _explain(*, log: Any, report: dict, persistent_store: Any, dry_run: bool) -> None:
    """MIR-074 (operator ruling): every pass explains itself in five points.

    «Что отодвинул и почему», not a bare counter dump. Deterministic; failure
    must not break the pass.

    The scoring rule quoted below lives in `core/memory_hygiene.py`. Prose and
    rule in two places can drift, and the census recorded that; keeping the
    words here is the lesser evil only while nothing checks them.
    """
    try:
        scanned = 0
        if persistent_store is not None:
            scanned = len(persistent_store.load())
        moved = (
            report.get("expired", 0)
            + report.get("deduped", 0)
            + report.get("archived", 0)
        )
        mode = "сухой прогон (только счёт)" if dry_run else "боевой проход"
        full_text = "\n".join((
            (
                f"Проверял: {scanned} записей постоянной памяти, "
                f"{report.get('episodes_pruned', 0)} эпизодов на прунинг, "
                "архив посылок на дубли и потолок"
            ),
            (
                "Способ: балл важности = вес тегов + причинный кредит "
                "(цитата в подтверждённом ответе, вчетверо против простой "
                "вставки) минус штраф простоя; автозапись-«факт» без "
                "причинного кредита больше не бессмертна"
            ),
            (
                f"Доказательство: {mode}; истекло {report.get('expired', 0)}, "
                f"дублей {report.get('deduped', 0)}, в спячку "
                f"{report.get('archived', 0)}, эпизодов срезано "
                f"{report.get('episodes_pruned', 0)}, дублей посылок "
                f"{report.get('assumptions_duplicates_removed', 0)}"
            ),
            (
                "Непроверенным осталось: полезность оставшихся записей не "
                "доказана и не опровергнута — спячка обратима, ничего не "
                "уничтожено"
            ),
            (
                f"Уверенность: высокая в счёте, {mode}; классификация "
                "«вредно/опровергнуто» — фаза 2, здесь не выносится"
            ),
        ))
        log.log(
            "hygiene_explained",
            {"full_text": full_text, "moved": moved, "dry_run": dry_run},
        )
    except Exception as exc:  # noqa: BLE001 — вердикт не должен ронять проход
        try:
            log.log(
                "hygiene_explained_failed",
                {"error_type": type(exc).__name__, "error": str(exc)[:300]},
            )
        except Exception:  # noqa: BLE001, S110 — страховка вокруг журналирования
            pass  # nosec B110 — последний рубеж: проход важнее записи о нём


def compact_assumptions(*, log: Any, assumption_store: Any, dry_run: bool = False) -> dict:
    """Dedupe and cap the assumptions archive (MIR-027's open half).

    The store became a dormant archive when the cross-turn auto-restore was
    removed; this keeps dormant BOUNDED — duplicates collapse to the newest row,
    the tail is capped — while retrieval stays with the memory-lifecycle
    contract.
    """
    if assumption_store is None:
        report = {
            "scanned": 0, "duplicates_removed": 0,
            "over_cap_removed": 0, "kept": 0, "dry_run": dry_run,
        }
    else:
        report = assumption_store.compact(dry_run=dry_run)
    log.log("assumptions_archive_compact", report)
    return report


def expire_persistent(*, log: Any, persistent_store: Any, dry_run: bool = False):
    """Remove persistent records whose TTL has elapsed."""
    from core.memory_hygiene import ExpiryReport, expire_memory

    if persistent_store is None:
        empty = ExpiryReport(dry_run=dry_run)
        log.log("persistent_memory_expire", empty.summary())
        return empty
    report = expire_memory(persistent_store, dry_run=dry_run)
    log.log("persistent_memory_expire", report.summary())
    return report


def dedupe_persistent(
    *, log: Any, persistent_store: Any,
    threshold: float | None = None, dry_run: bool = False,
):
    """Collapse near-duplicate persistent records (oldest kept)."""
    from core.memory_hygiene import (
        DEFAULT_DEDUP_THRESHOLD,
        DedupReport,
        deduplicate_memory,
    )

    resolved = DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold
    if persistent_store is None:
        empty = DedupReport(threshold=resolved, dry_run=dry_run)
        log.log("persistent_memory_dedupe", empty.summary())
        return empty
    report = deduplicate_memory(persistent_store, threshold=resolved, dry_run=dry_run)
    log.log("persistent_memory_dedupe", report.summary())
    return report


def prune_episodic(
    *,
    log: Any,
    episodic_store: Any,
    max_age_days: int = 30,
    min_quality: float = 0.4,
    staleness_threshold: float = 1.5,
    dry_run: bool = False,
) -> list[str]:
    """Evict old, low-quality, non-protected episodes from episodic memory.

    Complements the persistent-memory hygiene chain: FIFO eviction alone keeps
    recent ``replan_exhausted`` failures around as retrieval distractors.
    Returns the IDs pruned (or, in dry-run, that would be). No-op when no
    episodic store is configured.
    """
    if episodic_store is None:
        log.log(
            "episodic_memory_prune",
            {"pruned": 0, "dry_run": dry_run, "skipped_reason": "no store"},
        )
        return []
    pruned_ids = episodic_store.prune_stale(
        max_age_days=max_age_days,
        min_quality=min_quality,
        staleness_threshold=staleness_threshold,
        dry_run=dry_run,
    )
    log.log(
        "episodic_memory_prune",
        {"pruned": len(pruned_ids), "dry_run": dry_run, "ids": pruned_ids},
    )
    return pruned_ids


def summarise_persistent(
    tag: str,
    *,
    log: Any,
    persistent_store: Any,
    model_router: Any,
    max_records: int | None = None,
    dry_run: bool = False,
):
    """Merge records sharing `tag` into a single summary via the LLM."""
    from core.memory_hygiene import (
        DEFAULT_SUMMARY_MAX_RECORDS,
        SummaryReport,
        summarise_memory,
    )

    if persistent_store is None:
        empty = SummaryReport(tag=tag, skipped_reason="no store", dry_run=dry_run)
        log.log("persistent_memory_summarise", empty.summary())
        return empty
    report = summarise_memory(
        persistent_store,
        model_router.for_role(ModelRole.MEMORY_SUMMARY),
        tag=tag,
        max_records=DEFAULT_SUMMARY_MAX_RECORDS if max_records is None else max_records,
        dry_run=dry_run,
    )
    log.log("persistent_memory_summarise", report.summary())
    return report


def archive_persistent(
    *,
    log: Any,
    persistent_store: Any,
    threshold: float | None = None,
    min_age_days: int | None = None,
    dry_run: bool = False,
):
    """Move low-importance, old records to the archive (never deleted)."""
    from core.memory_hygiene import (
        DEFAULT_ARCHIVE_MIN_AGE_DAYS,
        DEFAULT_ARCHIVE_THRESHOLD,
        ArchiveReport,
        archive_low_value_memory,
    )

    resolved_threshold = (
        DEFAULT_ARCHIVE_THRESHOLD if threshold is None else threshold
    )
    resolved_age = (
        DEFAULT_ARCHIVE_MIN_AGE_DAYS if min_age_days is None else min_age_days
    )
    if persistent_store is None:
        empty = ArchiveReport(
            threshold=resolved_threshold,
            min_age_days=resolved_age,
            dry_run=dry_run,
        )
        log.log("persistent_memory_archive", empty.summary())
        return empty
    report = archive_low_value_memory(
        persistent_store,
        threshold=resolved_threshold,
        min_age_days=resolved_age,
        dry_run=dry_run,
    )
    log.log("persistent_memory_archive", report.summary())
    return report
