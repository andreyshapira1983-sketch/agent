"""Запись памяти циклом — и право на неё.

Право проверяется здесь, а не в модуле чтения: выборка не должна иметь
возможности разрешить себе запись.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.failure_cards import learn_after_turn
from core.run_context import current_run
from core.smart_memory import (
    admit_for_storage,
    episode_from_agent_cycle,
    resolve_used_procedures,
)

# Every durable sink the loop can write. A name outside this set is refused,
# so a typo or an unregistered sink fails closed.
KNOWN_DURABLE_SINKS: frozenset[str] = frozenset({
    "episode",          # episodic_store.save
    "procedure",        # procedural_store.upsert_from_episode
    "consolidation",    # retired (MIR-044): no write site names it;
                        # kept so an old config naming it still validates
    "knowledge",        # knowledge pipeline auto-write / remember batch
    "source_registry",  # source_registry_store
    "profile",          # user_profile_store
    "assumptions",      # assumption_store
    "access_stats",     # persistent record access_count / last_accessed_at
    "hygiene",          # expire / dedup / prune / archive — a DESTRUCTIVE write
})


class AgentLoopMemoryWrite:
    """Долговременная запись и право на неё; члены под TYPE_CHECKING — контракт хоста."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        consolidation_store: Any
        write_policy: Any
        # Из `loop_sensor`.
        _sensor_failed: Any
        # Из `loop_verification`.
        last_confidence_vector: Any
        # None — хранилище не заведено.
        causal_store: Any
        # Из `loop_memory_commands`: вывод эпизода идёт той же дорогой, что `:remember`.
        remember: Any

    def _unattended_run(self) -> bool:
        """True when nobody is at the keyboard for this run (`gateway_path` is not `repl`).

        Decided here, per run, so every write site gets the same answer.
        """
        return str(getattr(self, "gateway_path", "repl")) != "repl"

    def _durable_learning_suppressed(self, sink: str | None = None) -> bool:
        """True when a durable learning write must be skipped.

        First match wins: audit read-only, dry run, unknown sink (logged —
        checked before the allowlist so a typo is refused on every profile),
        no allowlist (allow), then the allowlist.
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
        """Наблюдаемая аномалия — в журнал и causal_store; причину и урок отсюда не выводим.

        Причину из сигнала вывести нельзя, поэтому тег `lesson` здесь не появляется.
        """
        try:
            from core.causal_lesson import observation_from_episode
            candidate = observation_from_episode(
                episode, trace_id=str(getattr(self.log, "trace_id", "") or "")
            )
            if candidate is None:
                return
            payload = candidate.to_log_payload()
            # Повтор схлопывается по отпечатку; `occurrences` отличает дефект от случайности.
            store = getattr(self, "causal_store", None)
            if store is not None and not self._durable_learning_suppressed("episode"):
                rec = store.record(candidate)
                payload["fingerprint"] = rec.fingerprint
                payload["occurrences"] = rec.occurrences
            self.log.log("causal_observation", payload)
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой в журнал
            self._sensor_failed("causal_observation", exc)

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
            learn_after_turn(self)  # карточки прошлых ошибок (core/failure_cards.py)
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
        run = current_run()
        # A `TypeError` here is a call-signature defect, not a memory fault, so
        # it propagates; logging it would silently bank nothing.
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
                # Тот же вектор, что печатает предупреждение оператору.
                relevance_score=getattr(
                    self.last_confidence_vector, "relevance_score", None),
                replan_exhausted=replan_exhausted,
                run_id=run.run_id if run else "",
                task_id=(run.task_id or "") if run else "",
                # Сшивает эпизод с продуктовым исходом того же прогона (MIR-184).
                trace_id=str(getattr(self.log, "trace_id", "") or ""),
                usage_eligible=None,   # resolved by `admit_for_storage` below
                # Attributed by what actually executed, never by workflow_key,
                # which pools unrelated goals (MIR-050).
                used_procedure_ids=resolve_used_procedures(
                    selected=list(getattr(self, "_last_procedure_records", []) or []),
                    executed_tools=list(getattr(self, "_executed_tools", []) or []),
                ),
                # Passed in, not read off `self`: instance state outlives its
                # run and would leak the previous run's verdict.
                declared_completion=declared_completion,
                # Sensor-accumulated and reset per run in `loop.py`; None means
                # "never collected", distinct from "none fired".
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
            episode = admit_for_storage(episode)
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
                # The sole credit path (MIR-049). Must run BEFORE upsert: upsert
                # records this episode id, and credit idempotency would then skip it.
                # A crashed verifier measured nothing (yet reads as `success`),
                # so it gets no positive credit and mints no procedure.
                feedback = self.procedural_store.apply_episode_feedback(
                    episode, allow_credit=not verifier_failure
                )
                # Merge is provenance-only; standing comes only from feedback above.
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

            # No per-cycle consolidation (MIR-044): the tally is computed on
            # demand by `:memory-consolidate` / `:smart-memory`.
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def _remember_conclusion(self, episode: Any) -> None:
        """Вывод допущенного эпизода — в долговременную память (`core/learned_conclusion.py`)."""
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
                return  # тот же вывод по тому же вопросу уже в памяти
            if not old:
                # Структурный ключ не решил — сверка по Mem0; любой сбой там — ADD.
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
                    # Слияние, а не замена: прежний вопрос, вывод обоих.
                    content = merged_content(content, old[0].content, consolidation.merged or "")
                else:
                    content = with_title(content, consolidation.title)
            decision, record = self.remember(
                content, tags, source="agent-auto", record_type="semantic", owner="self",
                existing=[r for r in existing if r not in old], supersedes=bool(old),
            )
            archived = ([r.id for r in old if self.persistent_store.archive_record(r.id)]
                        if record is not None else [])
        except Exception as exc:  # noqa: BLE001 — опыт уже записан; сбой памяти — в журнал
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

    def _consolidate_conclusion(self, content: str, episode: Any, existing: list[Any]) -> Any:
        """Решение Mem0 по новому выводу: ADD / UPDATE / DELETE / NOOP."""
        from core.memory_consolidation import consolidate, gate, similar_conclusions

        question = " ".join(str(getattr(episode, "question", "") or "").split())
        similar = similar_conclusions(content, existing)
        # Модель предлагает, ворота решают (memory_consolidation.gate).
        return gate(consolidate(getattr(self, "llm", None), content, question, similar), content, similar)

    def _record_aborted_episode(self, question: str, *, reason: str) -> None:
        """Bank a `failed` episode for a run that did not complete.

        Runs while an exception propagates, so it must never raise its own.
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
        """Withdraw memory records whose claim just turned out contradicted; best-effort.

        Matched by claim link only, never by content (MIR-049/050).
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
                # One rewrite total: `update` rewrites the whole file per record.
                self.persistent_store.update_many(
                    record for record in updated
                    if "conflicted" in (record.tags or [])
                )
            self.log.log("conflict_quarantine", {"claims": len(conflicted_ids), **report})
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "conflict_quarantine_error", {"error": type(exc).__name__}
            )
