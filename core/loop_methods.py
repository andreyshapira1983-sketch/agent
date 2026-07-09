"""Methods extracted verbatim from ``AgentLoop`` in ``core/loop.py`` by the
incremental splitter. The class inherits this mixin, so behaviour and the
public surface are unchanged."""

from __future__ import annotations

from pathlib import Path
from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWriteDecision,
    MemoryWritePolicy,
)
from core.memory_echo_antibody import make_event
from core.models import (
    Action,
    ApprovalRequest,
    ErrorObject,
    Goal,
    MemoryRecord,
    Observation,
    Plan,
    PlanStep,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from core.redaction import (
    collect_pii_findings,
    redact_dlp_text,
    redact_payload,
    scan,
)
from core.model_router import ModelRole, ModelRouter
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult, RememberFn

class AgentLoopExtractedMethods:
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
        workspace_root: "Path",
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ):
        """Generate a guarded RepairProposal without applying it."""
        from core.repair_proposal import RepairProposalGenerator

        return RepairProposalGenerator(
            workspace_root=workspace_root,
            llm=self.model_router.for_role(ModelRole.REPAIR_PROPOSAL),
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
        proposal: "Any",
        *,
        workspace_root: "Path",
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
        workspace_root: "Path | None" = None,
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
