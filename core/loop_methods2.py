"""Methods extracted verbatim from ``AgentLoop`` in ``core/loop.py`` by the
incremental splitter. The class inherits this mixin, so behaviour and the
public surface are unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
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
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
    episode_from_agent_cycle,
    format_experience_context,
)

class AgentLoopExtractedMethods2:
    def _durable_learning_suppressed(self) -> bool:
        """True when durable learning writes must be skipped this cycle.

        Combines the dry-run brake (`suppress_durable_learning_writes`) with
        the deterministic audit/read-only brake so both are honoured at every
        durable-write site with one check.
        """
        return bool(getattr(self, "suppress_durable_learning_writes", False)) or bool(
            getattr(self, "audit_read_only", False)
        )

    def set_audit_read_only(self, enabled: bool) -> bool:
        """Enable/disable audit read-only mode. Returns the resulting state.

        Idempotent. Engaging it (a) blocks every durable learning write via
        `_durable_learning_suppressed` and (b) freezes 'agent-auto' writes on
        the shared write policy (covering persistent/semantic writes and, on
        the autonomous path, reflection which reads `frozen_sources` live).
        Operator `:remember` (source='user-explicit') is never frozen, so the
        human keeps explicit control during an audit.
        """
        enabled = bool(enabled)
        if enabled == self.audit_read_only:
            return self.audit_read_only
        if enabled:
            self.audit_read_only = True
            froze = False
            if self.write_policy is not None:
                froze = self.write_policy.add_frozen_source("agent-auto")
            self._audit_froze_agent_auto = froze
            self.log.log(
                "audit_read_only_enabled",
                {"agent_auto_frozen_by_audit": froze},
            )
        else:
            self.audit_read_only = False
            if self._audit_froze_agent_auto and self.write_policy is not None:
                self.write_policy.remove_frozen_source("agent-auto")
            self._audit_froze_agent_auto = False
            self.log.log("audit_read_only_disabled", {})
        return self.audit_read_only

    def _episodic_store_mtime(self) -> float:
        """Return the modification time of the episodic store file, or 0.0."""
        if self.episodic_store is None:
            return 0.0
        try:
            return self.episodic_store.path.stat().st_mtime
        except OSError:
            return 0.0

    def _retrieve_persistent(self, question: str) -> str:
        """Pick + format relevant persistent records for prompt injection.

        Returns a `<long_term_memory>` XML block, or empty string when no
        store is wired, no records exist, or none score above threshold.
        """
        # MVP-14.1: stash the selected records so the evidence chain
        # can include them after run() finishes. Reset every cycle.
        self._last_persistent_records = []
        if self.persistent_store is None:
            return ""
        records = self.persistent_store.load()
        if not records:
            return ""
        use_report = self.knowledge_use_policy.filter(
            records,
            role_context=self.last_role_context,
            question=question,
        )
        self.log.log("knowledge_use_policy", use_report.to_log_payload())
        if not use_report.allowed:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no records applicable to current role",
                    "role": self.last_role_context.role,
                },
            )
            return ""
        selected = self.retrieval_policy.select(use_report.allowed, question)
        if not selected:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no keyword overlap above threshold",
                    "role": self.last_role_context.role,
                    "records_applicable": len(use_report.allowed),
                },
            )
            return ""
        formatted = self.retrieval_policy.format_for_prompt(selected)
        self.log.log(
            "persistent_memory_inject",
            {
                "records_total": len(records),
                "records_selected": len(selected),
                "records_applicable": len(use_report.allowed),
                "ids": [r.id for r in selected],
                "chars": len(formatted),
                "role": self.last_role_context.role,
            },
        )
        self._last_persistent_records = list(selected)

        # Bump access stats on every retrieved record so the archive scorer
        # knows which records are actively useful (≠ junk that was saved but
        # never used again).
        if (
            not self._durable_learning_suppressed()
            and self.persistent_store is not None
            and selected
        ):
            from datetime import datetime, timezone as _tz
            _now_dt = datetime.now(_tz.utc)
            for rec in selected:
                updated = rec.model_copy(update={
                    "access_count": rec.access_count + 1,
                    "last_accessed_at": _now_dt,
                })
                self.persistent_store.update(updated)

        return f"<long_term_memory>\n{formatted}\n</long_term_memory>"

    def _retrieve_experience_memory(self, question: str) -> str:
        """Inject compact episodic/procedural memory into planning.

        This is deliberately separate from `<long_term_memory>`:
        persistent memory stores user-approved facts, while experience memory
        stores operational history and reusable workflows. It can guide the
        planner without becoming a source of factual claims in the final answer.

        Re-ask detection: if the current question is very similar (Jaccard ≥ 0.4)
        to the stored *question* field of a past episode, the user is likely asking
        AGAIN because the previous answer was insufficient.  A
        ``<repeat_question_hint>`` block is appended to signal this to the planner.
        """
        self._last_episode_records = []
        self._last_procedure_records = []
        self._last_best_similar_episode = None
        self._last_best_similar_score = 0.0
        if self.episodic_store is None and self.procedural_store is None:
            return ""
        episodes = (
            self.episodic_store.search(question, limit=3)
            if self.episodic_store is not None
            else []
        )
        # ── Surface repair lessons for files mentioned in the question ────
        # search() gives a +50 boost to protected-tag episodes so they usually
        # appear in the top-3, but when the question contains a file path that
        # exactly matches a lesson's summary we fetch them explicitly as a
        # fallback — e.g. ":repair core/foo.py" should always see lessons
        # about core/foo.py even if the token overlap is otherwise low.
        if self.episodic_store is not None:
            lessons = self.episodic_store.search_by_tags(["lesson"], limit=5)
            q_lower = question.lower()
            # Extract path-like tokens: words containing "/" or ending in ".py"
            path_tokens = [
                w.strip("\"',:;()")
                for w in q_lower.split()
                if "/" in w or w.endswith(".py")
            ]
            for lesson in lessons:
                if lesson not in episodes and path_tokens and any(
                    tok in lesson.summary.lower() for tok in path_tokens
                ):
                    episodes.append(lesson)
        procedures = (
            self.procedural_store.search(question, limit=3)
            if self.procedural_store is not None
            else []
        )
        block = format_experience_context(episodes=episodes, procedures=procedures)
        self.log.log(
            "experience_memory_inject",
            {
                "episodes_selected": len(episodes),
                "procedures_selected": len(procedures),
                "episode_ids": [ep.id for ep in episodes],
                "procedure_ids": [proc.id for proc in procedures],
                "chars": len(block),
            },
        )
        self._last_episode_records = list(episodes)
        self._last_procedure_records = list(procedures)

        # ── Re-ask detection ──────────────────────────────────────────────
        # Jaccard similarity on the question tokens only (not goal/summary).
        # High overlap (≥ 0.40) means the user is asking the SAME question
        # again — a strong signal that the previous answer was insufficient.
        _REPEAT_THRESHOLD = 0.40
        if self.episodic_store is not None:
            try:
                repeat_ep, repeat_score = self.episodic_store.find_most_similar(
                    question, threshold=_REPEAT_THRESHOLD
                )
                # Store for fast-path and planner-cache checks in run().
                self._last_best_similar_episode = repeat_ep
                self._last_best_similar_score = repeat_score
                if repeat_ep is not None:
                    quality_pct = int(repeat_ep.answer_quality_score * 100)
                    quality_note = (
                        f"previous answer quality: {quality_pct}% verified — "
                        + ("HIGH" if quality_pct >= 70 else ("MEDIUM" if quality_pct >= 40 else "LOW"))
                    )
                    if quality_pct >= 70:
                        conclusion = (
                            "The previous answer was HIGH quality. "
                            "The user may be re-testing, want more detail, or verifying consistency. "
                            "You MAY confirm the previous answer if nothing has changed, "
                            "but try to add depth or perspective not present before."
                        )
                    else:
                        conclusion = (
                            "The previous answer was likely INSUFFICIENT or INCOMPLETE. "
                            "Do NOT repeat the same approach."
                        )
                    hint = (
                        "<repeat_question_hint>\n"
                        "WARNING: The user is asking a question that is very similar to a "
                        "previously answered one (Jaccard similarity "
                        f"{repeat_score:.2f} ≥ {_REPEAT_THRESHOLD}).\n"
                        f"Past episode: {repeat_ep.id} | outcome={repeat_ep.outcome}\n"
                        f"Past answer quality: {quality_note}\n"
                        f"Previous question: {repeat_ep.question[:200]}\n"
                        f"Previous answer summary: {repeat_ep.summary[:300]}\n"
                        f"CONCLUSION: {conclusion}\n"
                        "Action: try a different strategy, use additional tools, go "
                        "deeper, or explicitly acknowledge what was missing before.\n"
                        "</repeat_question_hint>"
                    )
                    self.log.log(
                        "repeat_question_detected",
                        {
                            "episode_id": repeat_ep.id,
                            "similarity": repeat_score,
                            "threshold": _REPEAT_THRESHOLD,
                            "past_outcome": repeat_ep.outcome,
                            "past_answer_quality": repeat_ep.answer_quality_score,
                            "past_question_chars": len(repeat_ep.question),
                        },
                    )
                    if block:
                        block = block + "\n\n" + hint
                    else:
                        block = hint
            except Exception:
                # Re-ask detection must never abort the main loop.
                pass

        return block

    def _record_experience_memory(
        self,
        *,
        goal_description: str,
        question: str,
        answer: str,
        tools_used: list[str],
        source_labels: list[str],
        verified_chunks: int,
        unverified_chunks: int,
        replan_exhausted: bool,
        weak_chunks: int = 0,
        skip_consolidation: bool = False,
    ) -> None:
        """Write episodic/procedural/consolidation memory after a cycle.

        Experience memory is best-effort. A malformed local state file should
        be quarantined by the state layer, not crash the user-facing answer.
        """
        if self._durable_learning_suppressed():
            self.log.log(
                "durable_learning_writes_skipped",
                {
                    "reason": "audit_read_only"
                    if getattr(self, "audit_read_only", False)
                    else "dry_run",
                    "sink": "experience_memory",
                },
            )
            return
        if (
            self.episodic_store is None
            and self.procedural_store is None
            and self.consolidation_store is None
        ):
            return
        try:
            episode = episode_from_agent_cycle(
                goal=goal_description,
                question=question,
                answer=answer,
                tools_used=tools_used,
                source_labels=source_labels,
                verified_chunks=verified_chunks,
                unverified_chunks=unverified_chunks,
                weak_chunks=weak_chunks,
                replan_exhausted=replan_exhausted,
            )
            if self.episodic_store is not None:
                self.episodic_store.save(episode)
                self.log.log(
                    "episodic_memory_write",
                    {
                        "episode_id": episode.id,
                        "outcome": episode.outcome,
                        "answer_quality_score": episode.answer_quality_score,
                        "tools_used": list(episode.tools_used),
                        "source_labels": list(episode.source_labels),
                        "verified_chunks": episode.verified_chunks,
                        "unverified_chunks": episode.unverified_chunks,
                        "weak_chunks": episode.weak_chunks,
                    },
                )

            procedure = None
            if self.procedural_store is not None:
                procedure, created = self.procedural_store.upsert_from_episode(episode)
                self.log.log(
                    "procedural_memory_update",
                    {
                        "episode_id": episode.id,
                        "procedure_id": procedure.id if procedure else None,
                        "created": created,
                        "status": procedure.status if procedure else "skipped",
                        "confidence": procedure.confidence if procedure else None,
                    },
                )

            if (
                self.consolidation_store is not None
                and self.episodic_store is not None
                and self.procedural_store is not None
                and not skip_consolidation
            ):
                report = consolidate_memory(
                    episodes=self.episodic_store.load(),
                    procedures=self.procedural_store.load(),
                )
                self.consolidation_store.save(report)
                self.log.log(
                    "memory_consolidation",
                    {
                        "report_id": report.id,
                        "episode_count": report.episode_count,
                        "procedure_count": report.procedure_count,
                        "active_procedure_ids": list(report.active_procedure_ids),
                        "needs_review_procedure_ids": list(report.needs_review_procedure_ids),
                        "notes": list(report.notes),
                    },
                )
            elif skip_consolidation and self.consolidation_store is not None:
                self.log.log(
                    "memory_consolidation_skipped",
                    {"reason": "cheap_path"},
                )
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def smart_memory_summary(self) -> dict[str, Any]:
        """Return local smart-memory counts for operator CLI commands."""
        episodes = self.episodic_store.load() if self.episodic_store else []
        procedures = self.procedural_store.load() if self.procedural_store else []
        reports = self.consolidation_store.load() if self.consolidation_store else []
        last_report = reports[-1].to_dict() if reports else None
        return {
            "episodic": {
                "path": str(self.episodic_store.path) if self.episodic_store else None,
                "episodes": len(episodes),
                "outcomes": {
                    outcome: sum(1 for ep in episodes if ep.outcome == outcome)
                    for outcome in ("success", "partial", "failed")
                },
            },
            "procedural": {
                "path": str(self.procedural_store.path) if self.procedural_store else None,
                "procedures": len(procedures),
                "statuses": {
                    status: sum(1 for proc in procedures if proc.status == status)
                    for status in ("active", "needs_review", "obsolete")
                },
            },
            "consolidation": {
                "path": str(self.consolidation_store.path) if self.consolidation_store else None,
                "reports": len(reports),
                "last_report": last_report,
            },
        }

    def _interpret(self, observation: Observation) -> Goal:
        question = observation.content["question"]
        return Goal(
            description=f"Answer the question: {question}",
            success_criteria="A grounded answer citing every claim back to a provided source.",
        )
