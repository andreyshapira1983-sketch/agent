"""Methods extracted verbatim from ``AgentLoop`` in ``core/loop.py`` by the
incremental splitter. The class inherits this mixin, so behaviour and the
public surface are unchanged."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.knowledge_pipeline import RememberFn
from core.memory_echo_antibody import MemoryWriteEvent, make_event
from core.memory_policy import (
    MemoryWriteDecision,
)
from core.model_router import ModelRole, ModelRouter
from core.models import (
    MemoryRecord,
)
from core.redaction import (
    redact_dlp_text,
)


class AgentLoopExtractedMethods:
    """Methods extracted from ``AgentLoop``, mixed back into it.

    Everything below runs against state that lives on the composed
    ``AgentLoop``, not here. The declarations that follow are annotations only —
    no assignment, so nothing is created or shadowed at runtime — and exist so a
    reader (and a static checker) can see what this mixin requires from its
    host. They are typed as the loop supplies them.

    Added when a review flagged ``self.model_router`` as an unknown member; the
    honest fix was not to annotate that one attribute but to state the whole
    contract, since eight are reached the same way.

    Only members this mixin does NOT define belong here. Declaring one it does
    define replaces the real signature with the stub for every static checker,
    which is how a loose ``**kwargs`` stub can hide a precise method.
    """

    if TYPE_CHECKING:  # pragma: no cover — declarations, never executed
        log: Any
        model_router: ModelRouter
        persistent_store: Any
        episodic_store: Any
        write_policy: Any
        memory_write_registry: Any
        compensation_log: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

    def _remember_from_knowledge(
        self,
        content: str,
        tags: list[str],
        source: str,
        record_type: str,
        owner: str,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        return self.remember(
            content=content,
            tags=tags,
            source=source,
            record_type=record_type,
            owner=owner,
        )

    def _knowledge_remember_batch(self) -> RememberFn:
        """Return a ``remember`` callback that loads the persistent store ONCE.

        The knowledge pipeline attempts a write for every extracted claim. On a
        repeated read-only turn (e.g. a work-session re-running the same goal)
        the same ~60 claims are re-extracted from the same files each cycle, and
        each `remember()` used to reload the entire store to run the dedup gate —
        an O(claims × records) disk reload every cycle whose writes are ~all
        rejected as duplicates (TD-019). This closure loads the snapshot a single
        time per pipeline pass and reuses it; ``remember`` keeps it current by
        appending any saved record, so dedup/echo behavior is unchanged.
        """
        snapshot: list[MemoryRecord] = (
            self.persistent_store.load() if self.persistent_store is not None else []
        )

        def _remember(
            content: str,
            tags: list[str],
            source: str,
            record_type: str,
            owner: str,
        ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
            return self.remember(
                content=content,
                tags=tags,
                source=source,
                record_type=record_type,
                owner=owner,
                existing=snapshot,
            )

        return _remember

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str = "user-explicit",
        record_type: str = "semantic",
        owner: str = "user",
        existing: list[MemoryRecord] | None = None,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        """Run a `:remember`-style write through the Write Policy.

        Returns the decision plus the saved record (or None on reject).
        The store must be wired; otherwise a reject decision is returned.

        `owner` flows into the Write Policy. Anything outside the first-
        party whitelist needs a `cross-owner-consent` tag.

        ``existing`` is an optional pre-loaded snapshot of the persistent
        records used by the dedup gate. When a caller writes many records in
        one pass (e.g. the knowledge pipeline attempting dozens of agent-auto
        claims per cycle) it can load the store ONCE and pass the same list in,
        avoiding a full store reload per write. When provided, any record saved
        here is appended to it so later writes in the same pass still dedup
        against it — matching the reload-every-time semantics exactly. When
        ``None`` (the default) the store is loaded fresh, as before.
        """
        if self.persistent_store is None:
            decision = MemoryWriteDecision(
                "reject", ["persistent store not configured"]
            )
            self.log.log("persistent_memory_write", {"decision": decision.decision, "reasons": decision.reasons})
            return decision, None

        tags = tags or []
        # Dedup gate (MVP-10) needs the existing records. Load them once and
        # pass into the policy so the policy stays a pure function. A caller
        # may supply the snapshot (TD-019) to avoid reloading per write.
        existing = self.persistent_store.load() if existing is None else existing
        # Echo gate (A1) needs the recent agent-auto write-log, time-windowed.
        # Only consulted when a registry is wired; stays a pure input to the
        # policy. `user-explicit` writes are never echo-guarded.
        recent_writes: list[MemoryWriteEvent] = []
        if self.memory_write_registry is not None:
            try:
                recent_writes = self.memory_write_registry.recent()
            except Exception:
                recent_writes = []  # Registry hiccup must never block a write.
        decision = self.write_policy.decide(
            content=content,
            tags=tags,
            source=source,
            owner=owner,
            existing=existing,
            recent_writes=recent_writes,
        )
        if decision.decision == "reject":
            self.log.log(
                "persistent_memory_write",
                {
                    "decision": "reject",
                    "reasons": decision.reasons,
                    "policy_id": decision.policy_id,
                    "source": source,
                    "tag_count": len(tags),
                    "content_chars": len(content or ""),
                },
            )
            return decision, None

        safe_content, _secret_findings, pii_findings = redact_dlp_text(content.strip())
        if pii_findings:
            self.log.log(
                "sensitive_detected",
                {
                    "label": "persistent_memory_candidate",
                    "kinds": sorted({f"pii-{f.kind}" for f in pii_findings}),
                    "count": len(pii_findings),
                    "surface": "persistent_memory",
                },
            )

        record = MemoryRecord(
            type=record_type,  # type: ignore[arg-type]
            content=safe_content.strip(),
            tags=tags,
            owner=owner,
            # MIR-074 root fix: persist the origin. Without it every stored
            # record read back as origin-less, and the MIR-046 independence
            # rule demoted every memory citation to topic-only — measured as
            # the all-history ZERO of verified memory citations.
            source=source,
        )
        self.persistent_store.save(record)
        # Keep a caller-supplied snapshot (TD-019) current so subsequent writes
        # in the same pass dedup against this record, exactly as a fresh reload
        # would have. Harmless when ``existing`` is a private freshly-loaded list.
        existing.append(record)
        # Record this agent-auto write in the rolling echo-log so the next
        # cycle's echo gate can see it. Only agent-auto writes are logged —
        # the registry exists to catch the agent echoing *itself*.
        if (
            self.memory_write_registry is not None
            and (source or "").strip().lower() == "agent-auto"
        ):
            try:
                self.memory_write_registry.append(
                    make_event(
                        record.content if isinstance(record.content, str) else str(record.content),
                        tags=tags,
                        record_type=record_type,
                        source=source,
                        cycle_id=getattr(self.log, "trace_id", "") or "",
                    )
                )
            except Exception:
                pass  # Echo-log write must never abort the memory write.
        self.log.log(
            "persistent_memory_write",
            {
                "decision": "save",
                "reasons": decision.reasons,
                "policy_id": decision.policy_id,
                "record_id": record.id,
                "tags": record.tags,
                "type": record.type,
                "chars": len(record.content) if isinstance(record.content, str) else 0,
            },
        )
        return decision, record

    def forget(self, record_id: str | None = None) -> int:
        """Delete one record (by id) or all records. Returns deletion count."""
        if self.persistent_store is None:
            return 0
        if record_id is None:
            n = self.persistent_store.delete_all()
            self.log.log("persistent_memory_delete", {"scope": "all", "deleted": n})
            return n
        ok = self.persistent_store.delete(record_id)
        self.log.log(
            "persistent_memory_delete",
            {"scope": "one", "record_id": record_id, "deleted": int(ok)},
        )
        return 1 if ok else 0

    def list_persistent(self) -> list[MemoryRecord]:
        if self.persistent_store is None:
            return []
        return self.persistent_store.load()

    def propose_repair(
        self,
        *,
        target_path: str,
        workspace_root: Path,
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ):
        """Generate a guarded RepairProposal without applying it.

        Routed through ``for_task``, not ``for_role``: rewriting a whole module
        correctly is the hardest thing here, and ``for_role`` cannot escalate no
        matter how large the target is. The tier is computed from the job (file
        size, red tests) rather than from the request's wording, then passed as
        ``force_tier`` — which the router still puts through the operator gate,
        so this asks for the deep model and never grants it. With no
        ``AGENT_DEEP_MAX_CALLS_PER_SESSION`` budget set, the ask is refused and
        the behaviour is identical to before.
        """
        from core.deep_escalation import (
            OperatorEscalation,
            deep_budget_ok,
            deep_call_budget,
        )
        from core.repair_proposal import RepairProposalGenerator, repair_complexity

        try:
            _target_chars = len(
                (Path(workspace_root) / target_path).read_text(
                    encoding="utf-8", errors="replace"
                )
            )
        except OSError:
            # Unreadable target is the generator's error to report, not ours;
            # size 0 simply means "no case for the expensive model".
            _target_chars = 0

        def _select_llm(failing_tests: int):
            """Pick the model once the baseline is known.

            Deferred on purpose: the failing-test count is half the difficulty
            signal and only exists after `generate()` runs the baseline. An
            earlier version passed a literal 0 here, which silently disabled
            that half — the logic and its tests existed while production always
            saw zero.
            """
            tier = repair_complexity(
                target_chars=_target_chars, failing_tests=failing_tests
            )
            limit = deep_call_budget()
            escalation = OperatorEscalation(
                reason="high_value_repair",
                expected_output="minimal_patch_plan",
                budget_ok=deep_budget_ok(self.model_router.usage_ledger, limit=limit),
                # Never true here. A human typing `--reason` is approving; an
                # autonomous repair is not, and marking it approved would skip
                # the budget check that makes this safe.
                operator_approved=False,
            )
            return self.model_router.for_task(
                ModelRole.REPAIR_PROPOSAL,
                f"repair {target_path}",
                escalation=escalation,
                force_tier=tier,
            )

        return RepairProposalGenerator(
            workspace_root=workspace_root,
            # Built from size alone, and only used if the generator never gets
            # as far as the baseline (e.g. an unreadable target).
            llm=self.model_router.for_role(ModelRole.REPAIR_PROPOSAL),
            llm_selector=_select_llm,
            logger=self.log,
        ).generate(
            target_path=target_path,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            extra_context=extra_context,
        )

    def repair(
        self,
        proposal: Any,
        *,
        workspace_root: Path,
    ):
        """Run one self-repair proposal through the MVP-13.2 controller."""
        from core.self_repair import SelfRepairController

        return SelfRepairController(
            self,
            workspace_root=workspace_root,
        ).run(proposal)

    def rollback(
        self,
        plan_id: str | None = None,
        *,
        workspace_root: Path | None = None,
    ):
        """Apply the most recent compensation plan (or one by id).

        Returns the `CompensationReport`. When the log is empty, the
        report carries zero outcomes and the audit event records the
        no-op. When `plan_id` is provided, the matching plan is
        removed from the log; otherwise the LAST plan is popped.

        `workspace_root` is required because `AgentLoop` is workspace-
        agnostic by construction. The CLI passes it from the main()
        --workspace argument; tests pass the same root the producing
        tool used.
        """
        from core.compensation import CompensationReport, apply_compensation_plan

        if workspace_root is None:
            report = CompensationReport(plan_id=plan_id or "", workspace_root="")
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no workspace_root supplied"},
            )
            return report

        if not self.compensation_log:
            report = CompensationReport(
                plan_id=plan_id or "", workspace_root=str(Path(workspace_root).resolve())
            )
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no plans registered"},
            )
            return report

        if plan_id is None:
            plan = self.compensation_log.pop()
        else:
            for i, p in enumerate(self.compensation_log):
                if p.id == plan_id:
                    plan = self.compensation_log.pop(i)
                    break
            else:
                report = CompensationReport(
                    plan_id=plan_id, workspace_root=str(Path(workspace_root).resolve())
                )
                self.log.log(
                    "compensation_apply",
                    {**report.summary(), "skipped_reason": f"plan_id '{plan_id}' not found"},
                )
                return report

        report = apply_compensation_plan(plan, Path(workspace_root))
        self.log.log("compensation_apply", report.summary())
        return report

    def cleanup_backups(
        self,
        workspace_root,
        *,
        keep_last: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Delete old `.bak.<ts>` files under the workspace root."""
        from core.hygiene import (
            DEFAULT_KEEP_LAST,
            DEFAULT_MAX_AGE_DAYS,
            cleanup_backups,
        )

        report = cleanup_backups(
            workspace_root,
            keep_last=DEFAULT_KEEP_LAST if keep_last is None else keep_last,
            max_age_days=DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days,
            dry_run=dry_run,
        )
        self.log.log("backup_cleanup", report.summary())
        return report

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
                f"Проверял: {scanned} записей постоянной памяти, "
                f"{report.get('episodes_pruned', 0)} эпизодов на прунинг, "
                "архив посылок на дубли и потолок",
                "Способ: балл важности = вес тегов + причинный кредит "
                "(цитата в подтверждённом ответе, ×4 против простой вставки) "
                "− штраф простоя; автозапись-«факт» без причинного кредита "
                "больше не бессмертна",
                f"Доказательство: {mode}; истекло {report.get('expired', 0)}, "
                f"дублей {report.get('deduped', 0)}, в спячку "
                f"{report.get('archived', 0)}, эпизодов срезано "
                f"{report.get('episodes_pruned', 0)}, дублей посылок "
                f"{report.get('assumptions_duplicates_removed', 0)}",
                "Непроверенным осталось: полезность неarchived записей не "
                "доказана и не опровергнута — спячка обратима, ничего не "
                "уничтожено",
                f"Уверенность: высокая в счёте, {mode}; классификация "
                "«вредно/опровергнуто» — фаза 2, здесь не выносится",
            ))
            self.log.log(
                "hygiene_explained",
                {"full_text": full_text, "moved": moved, "dry_run": dry_run},
            )
        except Exception as _hx_exc:
            try:
                self.log.log(
                    "hygiene_explained_failed",
                    {
                        "error_type": type(_hx_exc).__name__,
                        "error": str(_hx_exc)[:300],
                    },
                )
            except Exception:  # noqa: S110 — last-resort guard around logging
                pass

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
