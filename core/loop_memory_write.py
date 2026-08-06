"""Запись памяти циклом — и право на неё.

Правило оператора: «разбирай большие файлы на компактные подключаемые модули».
Этот модуль — вторая половина `core/loop_methods2.py`, которого больше нет
(почему он так назывался и что там было — см. `core/loop_memory_read.py`).

Здесь три записи и два гейта над ними. `KNOWN_DURABLE_SINKS` перечисляет ВСЕ
долговременные приёмники, куда цикл вправе писать: место записи называет свой
приёмник, и имя вне набора отвергается, а не пропускается — опечатка или новый
незарегистрированный приёмник падают закрыто.

Право проверяется здесь же (`_durable_learning_suppressed`, `set_audit_read_only`)
и намеренно не уехало к чтению: выборка не должна иметь возможности разрешить
себе запись.

Методы перенесены дословно; сверка с историей — в
`tests/test_loop_memory_split.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.run_context import current_run
from core.smart_memory import (
    admit_for_storage,
    consolidate_memory,
    episode_from_agent_cycle,
    resolve_used_procedures,
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


class AgentLoopMemoryWrite:
    """Долговременная запись и право на неё.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        consolidation_store: Any
        write_policy: Any

    def _unattended_run(self) -> bool:
        """True when nobody is at the keyboard for this run.

        Read from `gateway_path`, which is what actually records who drives the
        cycle: `repl` is a human typing, `daemon` and `runtime` are not.
        `AutonomousRuntime` sets it before a goal and restores it after, so it
        is per-run rather than a property of the agent object.

        This exists because `require_verified` used to be decided per CALL SITE
        — true where the learning path called the knowledge pipeline, absent
        where the turn path did. The unattended runtime drives the ordinary
        cycle, so it reached the sites that had no gate. Deciding it here means
        one answer to one question, asked in the only place that knows it.
        """
        return str(getattr(self, "gateway_path", "repl")) != "repl"

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
        # Building the episode is guarded separately from writing it, because a
        # `TypeError` here is a CALL-SIGNATURE DEFECT, not a memory fault: the
        # factory was invoked with an argument it does not accept (or without
        # one it requires). Laundering that into a `smart_memory_error` log line
        # hid the defect completely — the cycle answered normally and simply
        # banked nothing. So `TypeError` from this call PROPAGATES; every other
        # failure of the factory stays best-effort, as before.
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
                usage_eligible=None,   # resolved by `admit_for_storage` below
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
                # Read off `self` deliberately, unlike `declared_completion`:
                # these are accumulated by the sensors as the cycle runs, not
                # produced by the synthesis this method owns. The reset in
                # `loop.py` is what keeps a previous run's faults from leaking
                # in. `getattr` with a default keeps a caller that never entered
                # the loop (a direct unit-test build) distinguishable — it gets
                # None, "never collected", rather than a false "none fired".
                defect_signals=getattr(self, "_defect_signals", None),
                on_audit=self.log.log,
            )
        except TypeError:
            # Visible on purpose — see the comment above.
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
            # The same helper the store applies. Called here so the write event
            # below can report the verdict that actually landed; the store's own
            # call is then a no-op, and no write site can skip the policy.
            episode = admit_for_storage(episode)
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
                        # Surfaced in the event too: an operator reading the
                        # journal should see the run's own faults next to its
                        # verdict, not have to open the episode store.
                        "defect_signals": (
                            None if episode.defect_signals is None
                            else list(episode.defect_signals)
                        ),
                        # The admission verdict was not reportable before: an
                        # operator reading this event could see what was banked
                        # but not whether anything would ever be allowed to read
                        # it back.
                        "usage_eligible": episode.usage_eligible,
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
                # Credit/debit the procedures this run actually USED — the SOLE
                # credit path (operator ruling 2026-08-02). Positive credit no
                # longer comes from upsert's tool-set match; it comes from here,
                # causally attributed (MIR-049), or not at all. Runs BEFORE
                # upsert on purpose: upsert now merges provenance without credit,
                # and the merge records this episode id — so it must not run
                # first, or the credit idempotency (episode id already in the
                # procedure's provenance) would skip the very credit this run
                # earned. Only the positive direction is withheld on a crash.
                feedback = self.procedural_store.apply_episode_feedback(
                    episode, allow_credit=not verifier_failure
                )
                # Create or merge the procedure distilled from this run. Merge is
                # provenance-only: a fresh candidate is born unproven and earns
                # standing only through the causal feedback above, never by a
                # repeated tool-set match.
                if not verifier_failure:
                    procedure, created = self.procedural_store.upsert_from_episode(episode)
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
                # An aborted run is exactly the one whose faults matter most:
                # whatever fired before the abort is the trail to why it died.
                defect_signals=getattr(self, "_defect_signals", None),
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
                # ONE rewrite for the whole quarantine, for the same reason as
                # the retrieval path (census A5): `update` rewrites the file per
                # record, so marking N contradicted records cost N rewrites.
                self.persistent_store.update_many(
                    record for record in updated
                    if "conflicted" in (record.tags or [])
                )
            self.log.log("conflict_quarantine", {"claims": len(conflicted_ids), **report})
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "conflict_quarantine_error", {"error": type(exc).__name__}
            )
