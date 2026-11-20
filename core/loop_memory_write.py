"""Loop-side memory writing and the permission for it.

The permission is checked here, not in the read module: a selection must not be able to authorise its own write.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from core.arithmetic_check import arithmetic_receipt
from core.failure_cards import learn_after_turn
from core.replay_freshness import input_digests, read_files
from core.run_context import current_run
from core.smart_memory import (
    admit_for_storage,
    episode_from_agent_cycle,
    resolve_used_procedures,
)

# Every durable sink the loop can write; a name outside this set is refused, so a typo or unregistered sink fails closed.
KNOWN_DURABLE_SINKS: frozenset[str] = frozenset({
    "episode",          # episodic_store.save
    "procedure",        # procedural_store.upsert_from_episode
    "consolidation",  # retired: no write site names it;
                        # kept so an old config naming it still validates
    "knowledge",        # knowledge pipeline auto-write / remember batch
    "source_registry",  # source_registry_store
    "profile",          # user_profile_store
    "assumptions",      # assumption_store
    "access_stats",     # persistent record access_count / last_accessed_at
    "hygiene",          # expire / dedup / prune / archive — a DESTRUCTIVE write
})


class AgentLoopMemoryWrite:
    """Durable writing and the right to it; members under TYPE_CHECKING are the host contract."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        consolidation_store: Any
        write_policy: Any
        # From `loop_sensor`.
        _sensor_failed: Any
        _file_read_workspace_root: Any
        # From `loop_verification`.
        last_confidence_vector: Any
        # None: store not set up.
        causal_store: Any
        # From `loop_memory_commands`: the episode conclusion goes the same road as `:remember`.
        remember: Any

    def _unattended_run(self) -> bool:
        """True when nobody is at the keyboard for this run (`gateway_path` is not `repl`); decided per run so every write site agrees."""
        return str(getattr(self, "gateway_path", "repl")) != "repl"

    def _durable_learning_suppressed(self, sink: str | None = None) -> bool:
        """True when a durable learning write must be skipped.

        First match wins: audit read-only, dry run, unknown sink (checked before the allowlist so a typo is refused on every profile), no allowlist (allow), then the allowlist.
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
        """Enable/disable audit read-only mode; returns the resulting state.

        Also freezes 'agent-auto' on the write policy; operator `:remember`
        ('user-explicit') stays writable during an audit.
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

    def _log_causal_observation(self, episode: Any) -> None:
        """Observable anomaly to the journal and causal_store; no cause or lesson is derived here (a cause cannot be inferred from a signal, so no `lesson` tag)."""
        try:
            from core.causal_lesson import observation_from_episode
            candidate = observation_from_episode(
                episode, trace_id=str(getattr(self.log, "trace_id", "") or "")
            )
            if candidate is None:
                return
            payload = candidate.to_log_payload()
            # A repeat merges by fingerprint; `occurrences` tells a defect from chance.
            store = getattr(self, "causal_store", None)
            if store is not None and not self._durable_learning_suppressed("episode"):
                rec = store.record(candidate)
                payload["fingerprint"] = rec.fingerprint
                payload["occurrences"] = rec.occurrences
            self.log.log("causal_observation", payload)
        except Exception as exc:  # noqa: BLE001 — observing sensor: failure goes to the journal
            self._sensor_failed("causal_observation", exc)

    def _computed_receipt(self, question: str, answer: str, tools_used: list[str], chunks: int) -> list[str]:
        """A tool-free answer with no evidence chunks at all (a replay has one) whose number is computed."""
        try:
            confirmed = not tools_used and not chunks and arithmetic_receipt(question, answer)
        except Exception as exc:  # noqa: BLE001 — a check that breaks confirms nothing; banking goes on
            self.log.log("arithmetic_check_failed", {"error": f"{type(exc).__name__}: {exc}"[:200]})
            confirmed = False
        return ["computed:arithmetic"] if confirmed else []

    def _with_input_digests(self, episode: Any, run: Any) -> Any:
        """Hashes of the files behind the answer, for the replay gate (core/replay_freshness.py)."""
        if not read_files(episode):
            return episode
        root = self._file_read_workspace_root() if hasattr(self, "_file_read_workspace_root") else None
        started = float(getattr(run, "started_at", 0.0) or 0.0) if run else 0.0
        return replace(episode, input_digests=input_digests(episode, root, started))

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
        verifier_failure: bool = False,
        declared_completion: str | None = None,
    ) -> None:
        """Write episodic/procedural memory after a cycle; best-effort, never crashes the answer."""
        may_episode = not self._durable_learning_suppressed("episode")
        may_procedure = not self._durable_learning_suppressed("procedure")
        if may_episode:
            learn_after_turn(self)  # cards of past errors (core/failure_cards.py)
        if not (may_episode or may_procedure):
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
        run, receipt = current_run(), self._computed_receipt(
            question, answer, tools_used, verified_chunks + unverified_chunks + weak_chunks)
        # A `TypeError` is a call-signature defect, not a memory fault: it propagates.
        try:
            episode = episode_from_agent_cycle(
                goal=goal_description,
                question=question,
                answer=answer,
                tools_used=tools_used,
                source_labels=[*source_labels, *receipt],
                verified_chunks=verified_chunks + len(receipt),
                unverified_chunks=unverified_chunks,
                weak_chunks=weak_chunks,
                # Same vector that prints the operator warning.
                relevance_score=getattr(
                    self.last_confidence_vector, "relevance_score", None),
                replan_exhausted=replan_exhausted,
                run_id=run.run_id if run else "",
                task_id=(run.task_id or "") if run else "",
                # Stitches the episode to the product outcome of the same run.
                trace_id=str(getattr(self.log, "trace_id", "") or ""),
                usage_eligible=None,   # resolved by `admit_for_storage` below
                # Attributed by what actually executed, never by workflow_key, which pools unrelated goals.
                used_procedure_ids=resolve_used_procedures(
                    selected=list(getattr(self, "_last_procedure_records", []) or []),
                    executed_tools=list(getattr(self, "_executed_tools", []) or []),
                ),
                # Passed in, not read off `self`: instance state outlives its run and would leak the previous run's verdict.
                declared_completion=declared_completion,
                # Sensor-accumulated and reset per run in `loop.py`; None means "never collected", distinct from "none fired".
                defect_signals=getattr(self, "_defect_signals", None),
                on_audit=self.log.log,
            )
        except TypeError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return
        try:
            # Called here too so the write event reports the verdict that landed.
            episode = admit_for_storage(self._with_input_digests(episode, run))
            if self.episodic_store is not None and may_episode:
                # save_once: a run reaching this site twice banks one episode.
                written = self.episodic_store.save_once(episode)
                if written:
                    self._log_causal_observation(episode)
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
                        "verification_subject": episode.verification_subject,
                        "draft_quality_score": episode.draft_quality_score,
                        "declared_completion": episode.declared_completion,
                        "completion_state": episode.completion_state,
                        "completion_override": episode.completion_override,
                        "tools_used": list(episode.tools_used),
                        "source_labels": list(episode.source_labels),
                        "verified_chunks": episode.verified_chunks,
                        "unverified_chunks": episode.unverified_chunks,
                        "weak_chunks": episode.weak_chunks,
                        "defect_signals": (
                            None if episode.defect_signals is None
                            else list(episode.defect_signals)
                        ),
                        "usage_eligible": episode.usage_eligible,
                    },
                )
                if written:
                    self._remember_conclusion(episode)

            procedure = None
            created = False
            if self.procedural_store is not None and may_procedure:
                # The sole credit path. Must run BEFORE upsert (upsert records this episode id and credit idempotency would skip it). A crashed verifier measured nothing, so it gets no positive credit and mints no procedure.
                feedback = self.procedural_store.apply_episode_feedback(
                    episode, allow_credit=not verifier_failure
                )
                # Merge is provenance-only; standing comes only from the feedback above.
                if not verifier_failure:
                    procedure, created = self.procedural_store.upsert_from_episode(episode)
                # `offered` separates "nothing suggested" from "suggested but not applied".
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

            # No per-cycle consolidation: the tally is computed on demand by `:memory-consolidate` / `:smart-memory`.
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def _remember_conclusion(self, episode: Any) -> None:
        """Conclusion of an admitted episode into long-term memory (`core/learned_conclusion.py`)."""
        from core.learned_conclusion import (
            TAGS,
            WEB_TAGS,
            conclusion_memory,
            superseded_by,
            web_knowledge_memory,
            web_knowledge_reason,
        )

        content, tags = conclusion_memory(episode), list(TAGS)
        if content is None:
            content = web_knowledge_memory(
                episode, getattr(self, "last_verification", None), getattr(self, "last_provenance", None))
            tags = list(WEB_TAGS)
            if content is None:
                self.log.log("web_knowledge_skipped", {
                    "episode_id": getattr(episode, "id", ""),
                    "reason": web_knowledge_reason(
                        episode, getattr(self, "last_verification", None),
                        getattr(self, "last_provenance", None)),
                })
        if content is None or self._durable_learning_suppressed("knowledge"):
            return
        consolidation = None
        try:
            existing = self.persistent_store.load() if self.persistent_store is not None else []
            old, known = superseded_by(content, existing)
            if known:
                return  # same conclusion for the same question already in memory
            if not old:
                # Structural key did not decide: Mem0-style check; any failure there means ADD.
                consolidation = self._consolidate_conclusion(content, episode, existing)
                if consolidation.operation == "NOOP":
                    self.log.log("conclusion_memory_write", {
                        "episode_id": episode.id, "decision": "noop",
                        "consolidation": consolidation.operation,
                        "target": consolidation.target_id, "reason": consolidation.reason,
                    })
                    return
                if consolidation.operation in ("UPDATE", "DELETE"):
                    old = [r for r in existing if r.id == consolidation.target_id]
                from core.memory_consolidation import merged_content, with_title
                if consolidation.operation == "UPDATE" and old:
                    # Merge, not replace: the earlier question, the conclusions of both.
                    content = merged_content(content, old[0].content, consolidation.merged or "")
                else:
                    content = with_title(content, consolidation.title)
            decision, record = self.remember(
                content, tags, source="agent-auto", record_type="semantic", owner="self",
                existing=[r for r in existing if r not in old], supersedes=bool(old),
            )
            archived = ([r.id for r in old if self.persistent_store.archive_record(r.id)]
                        if record is not None else [])
            self._mark_replaced(record, existing, old)
        except Exception as exc:  # noqa: BLE001 — the experience is already recorded; a memory failure goes to the journal
            self._sensor_failed("conclusion_memory", exc)
            return
        self.log.log("conclusion_memory_write", {
            "episode_id": episode.id,
            "decision": decision.decision,
            "reasons": list(decision.reasons),
            "record_id": record.id if record is not None else None,
            "kind": "web-knowledge" if "web-knowledge" in tags else "conclusion",
            "superseded": archived,
            "consolidation": consolidation.operation if consolidation else "structural",
            "consolidation_reason": consolidation.reason if consolidation else "",
        })

    def _mark_replaced(self, record: Any, existing: list[Any], old: list[Any]) -> None:
        """Earlier similar conclusions that the new one replaced get its id; they are not hidden."""
        from core.jev_judge import THRESHOLD, jev_key, replacement_probabilities
        from core.memory_consolidation import similar_conclusions

        key = jev_key()
        if record is None or key is None:
            return
        gone = {r.id for r in old} | {record.id}
        try:
            candidates = [r for r in similar_conclusions(str(record.content), existing)
                          if r.id not in gone and not r.archived and r.superseded_by is None]
            if not candidates:
                return
            p = replacement_probabilities(key, str(record.content), candidates)
            marked = [r for r in candidates if p[r.id] >= THRESHOLD]
            # Only the one field, on the CURRENT record.
            self.persistent_store.patch_many(
                {r.id: {"superseded_by": record.id} for r in marked})
        except Exception as exc:  # noqa: BLE001 — Jev only marks, the conclusion is already recorded; failure goes to the journal
            self._sensor_failed("jev_memory", exc)
            return
        self.log.log("jev_memory_marks", {
            "record_id": record.id, "p": p, "marked": [r.id for r in marked]})

    def _consolidate_conclusion(self, content: str, episode: Any, existing: list[Any]) -> Any:
        """Mem0 decision on a new conclusion: ADD / UPDATE / DELETE / NOOP."""
        from core.memory_consolidation import consolidate, gate, similar_conclusions

        question = " ".join(str(getattr(episode, "question", "") or "").split())
        similar = similar_conclusions(content, existing)
        # The model proposes, the gate decides (memory_consolidation.gate).
        return gate(consolidate(getattr(self, "llm", None), content, question, similar), content, similar)

    def _record_aborted_episode(self, question: str, *, reason: str) -> None:
        """Bank a `failed` episode for a run that did not complete; runs while an exception propagates, so it must never raise its own."""
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
                defect_signals=getattr(self, "_defect_signals", None),
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

    def _quarantine_conflicted_memory(self, knowledge_result: Any) -> None:
        """Withdraw memory records whose claim just turned out contradicted; best-effort, matched by claim link only, never by content."""
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
                # One rewrite total, and only the tag is added to the CURRENT record (a stale whole copy reverted other fields).
                self.persistent_store.patch_many({
                    record.id: {"tags": lambda tags: [*(tags or []), "conflicted"]
                                if "conflicted" not in (tags or []) else list(tags)}
                    for record in updated if "conflicted" in (record.tags or [])
                })
            self.log.log("conflict_quarantine", {"claims": len(conflicted_ids), **report})
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "conflict_quarantine_error", {"error": type(exc).__name__}
            )
