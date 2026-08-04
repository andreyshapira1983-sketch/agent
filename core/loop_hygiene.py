"""Гигиена памяти: один проход обслуживания и шесть его шагов.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

И это не код цикла: `run_maintenance_pass` зовёт `agent_tick.py`, отдельные
шаги — `cli/commands_memory.py`.

Шаги намеренно доступны и по отдельности: проход целиком удобен автоматике,
а оператору бывает нужно ровно одно — просрочить, схлопнуть дубли, подрезать
эпизоды или заархивировать. Один вход на всё лишил бы его этой возможности.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.model_router import ModelRole


class AgentLoopHygiene:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        model_router: Any

    def run_maintenance_pass(self, *, dry_run: bool = True) -> dict:
        """One bounded hygiene pass: expire → dedup → prune episodes → archive.

        Exists so the unattended agent keeps its own memory: every operation
        below already existed, but the sole caller was the `:hygiene` command,
        and nobody types that on the daemon path (MIR-045).

        Governed as a **durable write** under the `hygiene` sink rather than by
        a bespoke permission check. That is what makes `:audit` and the dry-run
        brake stop it absolutely, and what stops a profile that was never
        granted the sink from running it — the same policy as every other
        write, with nothing extra to keep in sync.

        `dry_run=True` (the default) counts and reports without removing.
        Thresholds tuned on synthetic data should be seen against a real store
        before they are allowed to delete from it.

        `summarise_persistent` is deliberately absent: it needs an LLM call, so
        it is neither free nor deterministic, and it stays an operator action.

        Returns a delta report — the only way an operator learns what ran while
        nobody was watching.
        """
        if self._durable_learning_suppressed("hygiene"):
            reason = (
                "audit_read_only"
                if getattr(self, "audit_read_only", False)
                else "dry_run_brake"
                if getattr(self, "suppress_durable_learning_writes", False)
                else "not_allowlisted"
            )
            self.log.log("maintenance_pass_skipped", {"reason": reason})
            return {"skipped": reason, "dry_run": dry_run}

        report: dict = {"skipped": None, "dry_run": dry_run}
        try:
            expiry = self.expire_persistent(dry_run=dry_run)
            report["expired"] = len(getattr(expiry, "expired", []) or [])

            dedup = self.dedupe_persistent(dry_run=dry_run)
            report["deduped"] = len(getattr(dedup, "deleted", []) or [])

            pruned = self.prune_episodic(dry_run=dry_run)
            report["episodes_pruned"] = len(pruned or [])

            archive = self.archive_persistent(dry_run=dry_run)
            report["archived"] = len(getattr(archive, "archived", []) or [])

            compacted = self.compact_assumptions(dry_run=dry_run)
            report["assumptions_duplicates_removed"] = int(
                compacted.get("duplicates_removed", 0)
            )
            report["assumptions_over_cap_removed"] = int(
                compacted.get("over_cap_removed", 0)
            )
        except Exception as exc:  # noqa: BLE001
            # Maintenance must never take the tick down with it: a corrupt
            # state file is a reason to skip cleanup, not to stop working.
            self.log.log(
                "maintenance_pass_error",
                {"error": type(exc).__name__, "detail": str(exc)[:200]},
            )
            report["error"] = type(exc).__name__

        self.log.log("maintenance_pass", report)

        # MIR-074 (operator ruling): every hygiene pass explains itself in
        # the shared five-point vocabulary — «что отодвинул и почему», not a
        # bare counter dump. Deterministic; failure must not break the pass.
        try:
            scanned = 0
            if self.persistent_store is not None:
                scanned = len(self.persistent_store.load())
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
            self.log.log(
                "hygiene_explained",
                {"full_text": full_text, "moved": moved, "dry_run": dry_run},
            )
        except Exception as _hx_exc:  # noqa: BLE001 — вердикт не должен ронять проход
            try:
                self.log.log(
                    "hygiene_explained_failed",
                    {
                        "error_type": type(_hx_exc).__name__,
                        "error": str(_hx_exc)[:300],
                    },
                )
            except Exception:  # noqa: BLE001, S110 — страховка вокруг самого журналирования: последний рубеж, глушить осознанно
                pass  # nosec B110 — см. причину строкой выше

        return report

    def compact_assumptions(self, *, dry_run: bool = False) -> dict:
        """Dedupe and cap the assumptions archive (MIR-027's open half).

        The store became a dormant archive when the cross-turn auto-restore
        was removed; this keeps dormant BOUNDED — duplicates collapse to the
        newest row, the tail is capped — while retrieval stays with the
        memory-lifecycle contract.
        """
        store = getattr(self, "assumption_store", None)
        if store is None:
            report = {
                "scanned": 0, "duplicates_removed": 0,
                "over_cap_removed": 0, "kept": 0, "dry_run": dry_run,
            }
        else:
            report = store.compact(dry_run=dry_run)
        self.log.log("assumptions_archive_compact", report)
        return report

    def expire_persistent(self, *, dry_run: bool = False):
        """Remove persistent records whose TTL has elapsed."""
        from core.hygiene import expire_memory

        if self.persistent_store is None:
            from core.hygiene import ExpiryReport

            empty = ExpiryReport(dry_run=dry_run)
            self.log.log("persistent_memory_expire", empty.summary())
            return empty
        report = expire_memory(self.persistent_store, dry_run=dry_run)
        self.log.log("persistent_memory_expire", report.summary())
        return report

    def dedupe_persistent(self, *, threshold: float | None = None, dry_run: bool = False):
        """Collapse near-duplicate persistent records (oldest kept)."""
        from core.hygiene import DEFAULT_DEDUP_THRESHOLD, deduplicate_memory

        if self.persistent_store is None:
            from core.hygiene import DedupReport

            empty = DedupReport(
                threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
                dry_run=dry_run,
            )
            self.log.log("persistent_memory_dedupe", empty.summary())
            return empty
        report = deduplicate_memory(
            self.persistent_store,
            threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_dedupe", report.summary())
        return report

    def prune_episodic(
        self,
        *,
        max_age_days: int = 30,
        min_quality: float = 0.4,
        staleness_threshold: float = 1.5,
        dry_run: bool = False,
    ) -> list[str]:
        """Evict old, low-quality, non-protected episodes from episodic memory.

        Complements the persistent-memory hygiene chain: FIFO eviction alone
        keeps recent ``replan_exhausted`` failures around as retrieval
        distractors. Returns the IDs pruned (or, in dry-run, that would be).
        No-op when no episodic store is configured.
        """
        if self.episodic_store is None:
            self.log.log(
                "episodic_memory_prune",
                {"pruned": 0, "dry_run": dry_run, "skipped_reason": "no store"},
            )
            return []
        pruned_ids = self.episodic_store.prune_stale(
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            dry_run=dry_run,
        )
        self.log.log(
            "episodic_memory_prune",
            {"pruned": len(pruned_ids), "dry_run": dry_run, "ids": pruned_ids},
        )
        return pruned_ids

    def summarise_persistent(
        self,
        tag: str,
        *,
        max_records: int | None = None,
        dry_run: bool = False,
    ):
        """Merge records sharing `tag` into a single summary via the LLM."""
        from core.hygiene import DEFAULT_SUMMARY_MAX_RECORDS, summarise_memory

        if self.persistent_store is None:
            from core.hygiene import SummaryReport

            empty = SummaryReport(tag=tag, skipped_reason="no store", dry_run=dry_run)
            self.log.log("persistent_memory_summarise", empty.summary())
            return empty
        report = summarise_memory(
            self.persistent_store,
            self.model_router.for_role(ModelRole.MEMORY_SUMMARY),
            tag=tag,
            max_records=DEFAULT_SUMMARY_MAX_RECORDS if max_records is None else max_records,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_summarise", report.summary())
        return report

    def archive_persistent(
        self,
        *,
        threshold: float | None = None,
        min_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Move low-importance, old records to the archive (never deleted)."""
        from core.hygiene import (
            DEFAULT_ARCHIVE_MIN_AGE_DAYS,
            DEFAULT_ARCHIVE_THRESHOLD,
            ArchiveReport,
            archive_low_value_memory,
        )

        if self.persistent_store is None:
            empty = ArchiveReport(
                threshold=DEFAULT_ARCHIVE_THRESHOLD if threshold is None else threshold,
                min_age_days=DEFAULT_ARCHIVE_MIN_AGE_DAYS if min_age_days is None else min_age_days,
                dry_run=dry_run,
            )
            self.log.log("persistent_memory_archive", empty.summary())
            return empty
        report = archive_low_value_memory(
            self.persistent_store,
            threshold=DEFAULT_ARCHIVE_THRESHOLD if threshold is None else threshold,
            min_age_days=DEFAULT_ARCHIVE_MIN_AGE_DAYS if min_age_days is None else min_age_days,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_archive", report.summary())
        return report
