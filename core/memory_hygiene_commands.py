"""Memory hygiene as operator and daemon commands: expire, dedupe, prune, archive.

Dependencies arrive as arguments; the permission verdict stays with the agent facade and is passed in as `suppressed_reason`.
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
    workspace: Any = None,
    procedural_store: Any = None,
) -> dict:
    """One bounded hygiene pass: expire, dedupe, prune episodes, archive; returns a delta report.

    `summarise_persistent` is absent on purpose: it needs an LLM call and stays an operator action.
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

        # anchors of procedure credit are counted here and not evicted
        protected = cited_episode_ids(procedural_store, episodic_store)
        report["episodes_protected_by_citation"] = len(protected)
        pruned = prune_episodic(
            log=log, episodic_store=episodic_store, dry_run=dry_run,
            keep_ids=protected,
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
        # maintenance must never take the tick down: skip cleanup, report the error type
        log.log(
            "maintenance_pass_error",
            {"error": type(exc).__name__, "detail": str(exc)[:200]},
        )
        report["error"] = type(exc).__name__

    # separate try: a memory failure does not cancel disk cleanup, and vice versa; dry_run counts without deleting
    if workspace is not None:
        try:
            from core.backup_cleanup import cleanup_backups

            sweep = cleanup_backups(workspace, dry_run=dry_run)
            report["backups_deleted"] = len(sweep.deleted)
            report["backups_kept"] = len(sweep.kept)
        except Exception as exc:  # noqa: BLE001 — same boundary as above
            log.log("backup_sweep_error", {"error": type(exc).__name__})
            report["backup_sweep_error"] = type(exc).__name__

    log.log("maintenance_pass", report)
    _explain(log=log, report=report, persistent_store=persistent_store, dry_run=dry_run)
    return report


def _explain(*, log: Any, report: dict, persistent_store: Any, dry_run: bool) -> None:
    """Every pass explains itself in five points: what was set aside and why. Deterministic; must not break the pass.

    The scoring rule quoted below lives in `core/memory_hygiene.py`; the wording here is not checked against it.
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
        # dormancy is reversible, deletion is not: do not claim "nothing destroyed" after deletions
        deleted = sum(int(report.get(k, 0) or 0) for k in (
            "expired", "deduped", "episodes_pruned",
            "assumptions_duplicates_removed", "assumptions_over_cap_removed",
            "backups_deleted",
        ))
        if deleted and dry_run:
            fate = (f"спячка обратима; боевой проход удалил бы навсегда: {deleted} "
                    "(истёкшие, дубли, эпизоды, посылки, копии), сейчас не удалено ничего")
        elif deleted:
            fate = (f"спячка обратима, но удалено навсегда: {deleted} "
                    "(истёкшие, дубли, эпизоды, посылки, копии) — их не вернуть")
        else:
            fate = "спячка обратима, ничего не уничтожено"
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
                f"доказана и не опровергнута — {fate}"
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
    except Exception as exc:  # noqa: BLE001 — the verdict must not break the pass
        try:
            log.log(
                "hygiene_explained_failed",
                {"error_type": type(exc).__name__, "error": str(exc)[:300]},
            )
        except Exception:  # noqa: BLE001, S110 — guard around logging
            pass  # nosec B110 — last resort: the pass matters more than its record


def compact_assumptions(*, log: Any, assumption_store: Any, dry_run: bool = False) -> dict:
    """Dedupe and cap the assumptions archive."""
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


#: Cap on protected anchors so protection cannot shrink the eviction window again: 60 = 30 % of it; newest cited win.
_MAX_CITED_PROTECTED = 60


def cited_episode_ids(procedural_store: Any, episodic_store: Any) -> frozenset[str]:
    """Episodes that anchor procedure credit and still exist in the store.

    Ids from `source_episode_ids` intersected with live episodes, newest `_MAX_CITED_PROTECTED` by `created_at`.
    """
    if procedural_store is None or episodic_store is None:
        return frozenset()
    cited: set[str] = set()
    for proc in procedural_store.load():
        cited.update(proc.source_episode_ids)
    alive = [ep for ep in episodic_store.load() if ep.id in cited]
    alive.sort(key=lambda ep: ep.created_at, reverse=True)
    return frozenset(ep.id for ep in alive[:_MAX_CITED_PROTECTED])


def prune_episodic(
    *,
    log: Any,
    episodic_store: Any,
    max_age_days: int = 30,
    min_quality: float = 0.4,
    staleness_threshold: float = 1.5,
    dry_run: bool = False,
    keep_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """Evict old, low-quality, non-protected episodes from episodic memory."""
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
        keep_ids=keep_ids,
    )
    log.log(
        "episodic_memory_prune",
        {"pruned": len(pruned_ids), "dry_run": dry_run, "ids": pruned_ids},
    )
    return pruned_ids


def dedupe_episodic(
    *, log: Any, episodic_store: Any, dry_run: bool = False, procedural_store: Any = None,
) -> list[str]:
    """Keep one copy of each repeated episode; anchors of procedure credit stay (same set prune gets)."""
    if episodic_store is None:
        log.log("episodic_memory_dedupe",
                {"dropped": 0, "dry_run": dry_run, "skipped_reason": "no store"})
        return []
    from core.episodic_hygiene import collapse_duplicate_episodes

    keep = cited_episode_ids(procedural_store, episodic_store) if procedural_store is not None else frozenset()
    dropped = collapse_duplicate_episodes(episodic_store, dry_run=dry_run, keep_ids=keep)
    log.log("episodic_memory_dedupe",
            {"dropped": len(dropped), "dry_run": dry_run, "ids": dropped})
    return dropped


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
