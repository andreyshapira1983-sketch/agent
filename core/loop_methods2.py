"""Methods extracted verbatim from ``AgentLoop`` in ``core/loop.py`` by the
incremental splitter. The class inherits this mixin, so behaviour and the
public surface are unchanged."""

from __future__ import annotations

from dataclasses import replace
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
from core.run_context import current_run
from core.smart_memory import (
    PROCEDURE_STATUSES,
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
    decide_usage_eligibility,
    effective_completion,
    resolve_used_procedures,
    episode_from_agent_cycle,
    format_experience_context,
    is_usage_eligible,
)

# Every durable sink the loop can write. A write site names its sink; a name
# outside this set is refused rather than waved through, so a typo or a new
# unregistered sink fails closed.
KNOWN_DURABLE_SINKS: frozenset[str] = frozenset({
    "episode",          # episodic_store.save
    "procedure",        # procedural_store.upsert_from_episode
    "consolidation",    # consolidate_memory -> consolidation_store
    "knowledge",        # knowledge pipeline auto-write / remember batch
    "source_registry",  # source_registry_store
    "profile",          # user_profile_store
    "assumptions",      # assumption_store
    "access_stats",     # persistent record access_count / last_accessed_at
    "hygiene",          # expire / dedup / prune / archive — a DESTRUCTIVE write
})


def _merge_rejection_reasons(*reports: dict[str, int]) -> dict[str, int]:
    """Combine `rejected_by` maps from the components that did the rejecting.

    Retrieval is a chain of deciders — a use policy, then a scoring policy,
    then the loop's own filters — and each one knows the cause of its own
    drops. Merging what they report is the alternative to the observer
    inferring causes from `len(before) - len(after)`, which cannot separate a
    cap from a floor and, measured on the live store, mislabelled 4 of 6
    realistic questions.

    Reasons repeated across stages are summed: an episode ranked out by the
    store's cap and one ranked out by the loop's cap were both ranked out by
    a cap, and a reader looking for "how much did the caps cost me" wants one
    number. Absent reasons stay absent rather than becoming zeros.
    """
    merged: dict[str, int] = {}
    for report in reports:
        for reason, count in (report or {}).items():
            if count:
                merged[reason] = merged.get(reason, 0) + count
    return merged


class AgentLoopExtractedMethods2:
    def _durable_learning_suppressed(self, sink: str | None = None) -> bool:
        """True when a durable learning write must be skipped.

        Resolution order — the first rule that applies wins:

        1. ``audit_read_only`` — ABSOLUTE deny. The operator is auditing memory;
           no per-sink permission may pierce this, so `:audit` keeps its
           zero-delta guarantee.
        2. ``suppress_durable_learning_writes`` (dry-run) — ABSOLUTE deny, for
           the same reason: a dry run must leave no trace.
        3. Unnamed or unrecognised ``sink`` — ALWAYS deny, and log it. Sink
           *validity* is a separate question from sink *permission*, and it is
           answered first: otherwise a typo would be refused only while an
           allowlist happened to be active, and waved through on the
           interactive profile. Reaching this branch means a caller passed a
           name that is not in `KNOWN_DURABLE_SINKS` — a code defect, hence
           the log line.
        4. ``durable_writes is None`` — allow (the sink is known by now). This
           is the interactive default and preserves the historical
           "write everything" behaviour.
        5. Otherwise the allowlist decides: on it → allow, off it → deny.

        `durable_writes` is instance-scoped: it is fixed at construction for
        the life of the agent, and there is deliberately no per-run API.
        """
        if bool(getattr(self, "audit_read_only", False)):
            return True
        if bool(getattr(self, "suppress_durable_learning_writes", False)):
            return True
        if sink is None or sink not in KNOWN_DURABLE_SINKS:
            self.log.log(
                "durable_write_unknown_sink",
                {"sink": sink, "known_sinks": sorted(KNOWN_DURABLE_SINKS)},
            )
            return True
        allowlist = getattr(self, "durable_writes", None)
        if allowlist is None:
            return False
        return sink not in allowlist

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
                    # The policy classified every rejection as it made it;
                    # `role_scope`, `quarantined` and `not_applicable` are
                    # three different diagnoses and used to be one number.
                    "rejected_by": use_report.rejected_by,
                },
            )
            return ""
        selection = self.retrieval_policy.select_with_report(use_report.allowed, question)
        selected = selection.selected
        rejected_by = _merge_rejection_reasons(use_report.rejected_by, selection.rejected_by)
        if not selected:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no keyword overlap above threshold",
                    "role": self.last_role_context.role,
                    "records_applicable": len(use_report.allowed),
                    "rejected_by": rejected_by,
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
                # Same vocabulary as experience retrieval: reasons, not records.
                "rejected_by": rejected_by,
            },
        )
        self._last_persistent_records = list(selected)

        # Bump access stats on every retrieved record so the archive scorer
        # knows which records are actively useful (≠ junk that was saved but
        # never used again).
        if (
            not self._durable_learning_suppressed("access_stats")
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
        # Holding the stores and being allowed to read them are separate
        # permissions. Returning early also leaves `_last_best_similar_episode`
        # unset, which structurally keeps the fast path from firing.
        # Counterfactual trace: WHY a record did not come back is where the
        # information is. `selected=0` alone cannot distinguish "nothing
        # matched" from "everything matched but was withheld", and those call
        # for opposite responses. Counted BY REASON, never per record — a
        # per-record trace would cost more than the retrieval it observes.
        rejected_by: dict[str, int] = {}
        if not getattr(self, "experience_retrieval", True):
            self.log.log(
                "experience_memory_inject",
                {
                    "episodes_selected": 0,
                    "procedures_selected": 0,
                    "episode_ids": [],
                    "procedure_ids": [],
                    "chars": 0,
                    "rejected_by": {"retrieval_disabled": 1},
                },
            )
            return ""
        if self.episodic_store is None and self.procedural_store is None:
            return ""
        # Only SUCCESSFUL episodes are fed back as reusable experience; a
        # `partial`/`failed` episode must not be surfaced as "what worked
        # before" (CORE-05/LPF-012 — the self-reinforcing loop). Curated
        # `lesson` episodes are kept regardless: they are learn-from-failure by
        # design. Over-fetch, then filter, then cap so up to 3 GOOD episodes
        # still surface even when some top matches were non-success.
        # `is_usage_eligible` is the second, independent filter: outcome asks
        # "did this go well", eligibility asks "is this episode allowed to
        # steer anything at all". Legacy and quarantined episodes stay stored
        # and auditable but never reach the planner.
        episodes = []
        readmitted = 0
        if self.episodic_store is not None:
            # `search_with_report` rather than `search`: the store drops
            # episodes for reasons only it can see (no token overlap, its own
            # cap), and counting only what it handed back is how
            # `selected=0, rejected_by={}` stayed reachable on 200 episodes.
            found = self.episodic_store.search_with_report(question, limit=6)
            rejected_by = _merge_rejection_reasons(rejected_by, found.rejected_by)
            for ep in found.episodes:
                if not (ep.outcome == "success" or "lesson" in ep.tags):
                    rejected_by["outcome"] = rejected_by.get("outcome", 0) + 1
                    continue
                # Checked here as well as at admission, not instead of it: the
                # stored `usage_eligible` bit was decided by whatever rule was
                # in force when the episode was banked, and this reader must
                # answer for its own use case. A `lesson` keeps its context
                # arm — surfacing a failure as a warning is the whole point of
                # the tag — but an ordinary episode has to have finished the
                # job before it may steer a later one.
                if "lesson" not in ep.tags and effective_completion(ep) != "achieved":
                    rejected_by["not_achieved"] = rejected_by.get("not_achieved", 0) + 1
                    continue
                if not is_usage_eligible(ep):
                    rejected_by["not_eligible"] = rejected_by.get("not_eligible", 0) + 1
                    continue
                if len(episodes) >= 3:
                    rejected_by["over_limit"] = rejected_by.get("over_limit", 0) + 1
                    continue
                episodes.append(ep)
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
                if (
                    is_usage_eligible(lesson)          # second door — same gate
                    and lesson not in episodes
                    and path_tokens
                    and any(tok in lesson.summary.lower() for tok in path_tokens)
                ):
                    episodes.append(lesson)
                    # This lesson was already charged to some rejection reason
                    # by the first pass, and which one is not knowable here
                    # without a per-record trace. Reported as its own number
                    # rather than guessed at and subtracted: with it, the
                    # reader reconciles as
                    # `selected - readmitted + sum(rejected_by) == candidates`.
                    readmitted += 1
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
                "rejected_by": rejected_by,
                # Absent means zero, like every reason key.
                **({"readmitted": readmitted} if readmitted else {}),
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
                # Third door, and the most dangerous one: this feeds the fast
                # path, which returns a stored answer verbatim in place of a
                # real cycle. An ineligible match is dropped here rather than
                # at the fast-path gate, so re-ask hints cannot lean on it
                # either.
                if repeat_ep is not None and not is_usage_eligible(repeat_ep):
                    repeat_ep, repeat_score = None, 0.0
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

    def _quarantine_conflicted_memory(self, knowledge_result: Any) -> None:
        """Withdraw memory records whose claim just turned out contradicted.

        Runs at write time, where the conflict is detected, so the window in
        which a contradicted claim still reads as ordinary evidence is as
        short as the cycle itself. Marking is by provenance tag only — a
        record with no claim link is left alone rather than matched by
        content, which would be the guess MIR-049/050 forbid.

        Best-effort: quarantine failing must not abort a user-facing answer.
        """
        conflicts = getattr(knowledge_result, "conflicts", None)
        conflicted_ids = {
            cid
            for conflict in (getattr(conflicts, "conflicts", None) or [])
            for cid in getattr(conflict, "claim_ids", ())
        }
        if not conflicted_ids or self.persistent_store is None:
            return
        if self._durable_learning_suppressed("knowledge"):
            return
        try:
            from core.knowledge_pipeline import quarantine_conflicted_records

            records = self.persistent_store.load()
            updated, report = quarantine_conflicted_records(
                records, conflicted_claim_ids=conflicted_ids
            )
            if report["quarantined"]:
                for record in updated:
                    if "conflicted" in (record.tags or []):
                        self.persistent_store.update(record)
            self.log.log("conflict_quarantine", {"claims": len(conflicted_ids), **report})
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "conflict_quarantine_error", {"error": type(exc).__name__}
            )

    def _record_aborted_episode(self, question: str, *, reason: str) -> None:
        """Bank a `failed` episode for a run that did not complete.

        Called from `run`'s exception paths. Without it an interrupted run
        leaves either nothing (invisible) or, worse, a success banked earlier
        in the cycle. Best-effort by construction: this runs while an exception
        is propagating, so it must never replace that exception with its own.
        """
        if self.episodic_store is None:
            return
        if self._durable_learning_suppressed("episode"):
            return
        try:
            run = current_run()
            episode = episode_from_agent_cycle(
                goal="(run aborted before completion)",
                question=question,
                answer="",
                tools_used=[],
                source_labels=[],
                run_id=run.run_id if run else "",
                task_id=(run.task_id or "") if run else "",
                aborted_reason=reason,
                # Same quarantine as any other episode written today.
                usage_eligible=False,
            )
            written = self.episodic_store.save_once(episode)
            self.log.log(
                "episodic_memory_write_aborted",
                {
                    "written": written,
                    "reason": reason,
                    "episode_id": episode.id,
                    "run_id": episode.run_id,
                    "outcome": episode.outcome,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {"error": type(exc).__name__, "where": "_record_aborted_episode"},
            )

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
        verifier_failure: bool = False,
        declared_completion: str | None = None,
    ) -> None:
        """Write episodic/procedural/consolidation memory after a cycle.

        Experience memory is best-effort. A malformed local state file should
        be quarantined by the state layer, not crash the user-facing answer.
        """
        # Episode, procedure and consolidation are three separate sinks: a path
        # may be allowed to bank an episode while procedural promotion and
        # consolidation stay off.
        may_episode = not self._durable_learning_suppressed("episode")
        may_procedure = not self._durable_learning_suppressed("procedure")
        may_consolidation = not self._durable_learning_suppressed("consolidation")
        if not (may_episode or may_procedure or may_consolidation):
            self.log.log(
                "durable_learning_writes_skipped",
                {
                    "reason": "audit_read_only"
                    if getattr(self, "audit_read_only", False)
                    else "dry_run"
                    if getattr(self, "suppress_durable_learning_writes", False)
                    else "not_allowlisted",
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
        run = current_run()
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
                run_id=run.run_id if run else "",
                task_id=(run.task_id or "") if run else "",
                # Admission is decided per episode, not by a global switch:
                # MIR-002/041/046 are fixed, so the blanket quarantine is
                # lifted — but only evidenced, completed, non-replay episodes
                # (or curated lessons) become reusable experience. Everything
                # else stays fail-closed, and `None` remains reserved for rows
                # that predate the field. See `decide_usage_eligibility`.
                usage_eligible=None,   # replaced below, once the record exists
                # Attribution comes from what actually executed, over the
                # procedures this run selected -- never matched by workflow_key,
                # which MIR-050 measured to pool unrelated goals. () is a real
                # answer here ("nothing applied"), distinct from a legacy None.
                used_procedure_ids=resolve_used_procedures(
                    selected=list(getattr(self, "_last_procedure_records", []) or []),
                    executed_tools=list(getattr(self, "_executed_tools", []) or []),
                ),
                # Passed in by the caller that owns the synthesis, never read
                # off `self`: instance state outlives its run, and the paths
                # that bank without synthesising would inherit the previous
                # run's verdict. A replay or a refusal declares nothing.
                declared_completion=declared_completion,
                on_audit=self.log.log,
            )
            episode = replace(
                episode, usage_eligible=decide_usage_eligibility(episode)
            )
            if self.episodic_store is not None and may_episode:
                # save_once, not save: a run that reaches this site twice must
                # bank one episode, not two. Bounded by the store's FIFO window.
                written = self.episodic_store.save_once(episode)
                if not written:
                    self.log.log(
                        "episodic_memory_write_skipped",
                        {
                            "reason": "already_banked_for_run",
                            "episode_id": episode.id,
                            "run_id": episode.run_id,
                        },
                    )
                self.log.log(
                    "episodic_memory_write",
                    {
                        "written": written,
                        "run_id": episode.run_id,
                        "task_id": episode.task_id,
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
            created = False
            if self.procedural_store is not None and may_procedure:
                # A verifier that threw measured nothing. Its soft-fail records
                # `verified=0, unverified=0`, which falls through the outcome
                # derivation to `success` — so without this the crash would
                # mint a procedure and raise its confidence on evidence that
                # was never taken. `usage_eligible` cannot help: the procedural
                # path does not read it.
                if not verifier_failure:
                    procedure, created = self.procedural_store.upsert_from_episode(episode)
                # Outcome feedback (MIR-048), driven only by the attribution
                # MIR-049 recorded. Runs AFTER upsert on purpose: a fresh
                # success already journalled this episode id, so idempotence
                # makes this a no-op there and it only adds what upsert cannot
                # express -- a `partial`/`failed` debit against the procedures
                # the run actually used.
                # Only the positive direction is withheld on a crash: a debit
                # comes from a structural failure the verifier had no part in.
                feedback = self.procedural_store.apply_episode_feedback(
                    episode, allow_credit=not verifier_failure
                )
                # `offered` closes the counterfactual: applied=0 alone cannot
                # distinguish "no procedure was suggested" from "two were
                # suggested and neither was actually applied" — the second is
                # a signal about retrieval quality, the first is not.
                offered = len(getattr(self, "_last_procedure_records", []) or [])
                self.log.log(
                    "procedure_feedback",
                    {"episode_id": episode.id, "offered": offered, **feedback},
                )
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
                may_consolidation
                and self.consolidation_store is not None
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
                    for status in PROCEDURE_STATUSES
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
