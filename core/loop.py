"""Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond.

`AgentLoop` composes the per-phase mixins from `core/loop_*.py`; every action
passes the Policy Gate, every artifact is labelled with its source.
"""
from __future__ import annotations

from asyncio import CancelledError
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.egress_flow import EgressLedger
from core.evidence import (
    ProvenanceChain,
)
from core.file_request_intent import (
    prepare_multi_file_review,
)
from core.ids import new_id
from core.replan import ReplanTrigger
from core.request_checklist import attach_checklist
from core.run_context import identity_provenance, run_scope

if TYPE_CHECKING:
    from core.approval_inbox import ApprovalInbox
# Ре-экспорт: тесты и соседи импортируют эти имена как `from core.loop import …`.
from core.answer_format import (  # noqa: F401 -- re-exported
    _ANSWER_CITATION_RE,
    _VERIF_MARKER_RE,
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    _strip_verification_markers,
    citation_for_evidence,
    file_scope_notice,
    format_allowed_citations_block,
    format_artifact,
    format_human_response,
    output_contract_requires_headers,
)
from core.completion_contract import derive_completion_contract
from core.ids import new_trace_id  # noqa: F401 -- re-exported
from core.injection_guard import _to_text, untrusted_scan_view  # noqa: F401 -- re-exported
from core.loop_attempt import AgentLoopAttempt, AttemptState
from core.loop_context import AgentLoopContext
from core.loop_evidence_chain import AgentLoopEvidenceChain
from core.loop_gates import AgentLoopGates
from core.loop_hygiene import AgentLoopHygiene
from core.loop_init import AgentLoopInit
from core.loop_knowledge import AgentLoopKnowledge
from core.loop_memory_commands import AgentLoopMemoryCommands
from core.loop_memory_read import AgentLoopMemoryRead
from core.loop_memory_write import AgentLoopMemoryWrite
from core.loop_observe import AgentLoopObserve
from core.loop_repair import AgentLoopRepair
from core.loop_response_deciders import AgentLoopResponseDeciders
from core.loop_run_tail import AgentLoopRunTail
from core.loop_sensor import AgentLoopSensor

# Шов импорта: имена живут в `core/loop_step_execution`, берут их отсюда. Без
# директивы ruff снимет импорт; её код тут словами не пишем — ruff читает его везде.
from core.loop_step_execution import (  # noqa: F401 -- шов импорта
    _TOOL_SOURCE_HINTS,
    _TRUSTED_INTERNAL_TOOLS,
    AgentLoopStepExecution,
    _step_trigger_tls,
)
from core.loop_synthesis import AgentLoopSynthesis, SynthesisState
from core.loop_verification import AgentLoopVerification
from core.loop_verify_replan import AgentLoopVerifyReplan, VerifyState
from core.models import (
    Plan,
)
from core.planner import PlannerOutput
from core.replan import DEFAULT_MAX_REPLAN_ATTEMPTS  # noqa: F401 -- re-exported

# `ReplanCode` (алиас FailureType) импортируют отсюда (tests/test_replan_audit.py).
from core.replan import FailureType as ReplanCode  # noqa: F401 — шов импорта
from core.smart_memory import (
    effective_completion,
    episode_tools,
)
from core.source_registry import SourceRegistry
from core.step_repetition import StepRepetitionTracker
from core.termination_guard import TerminationGuard

try:
    from core.prompt_registry import register_prompt as _rp
    _rp("synthesizer.system", SYSTEM_ANSWER, module="core.loop",
        description="Output contract for the LLM synthesizer (§3 Cognitive Core)")
except ImportError:  # pragma: no cover
    pass


class AgentLoop(
    AgentLoopStepExecution,
    AgentLoopAttempt,
    AgentLoopContext,
    AgentLoopEvidenceChain,
    AgentLoopGates,
    AgentLoopObserve,
    AgentLoopResponseDeciders,
    AgentLoopRunTail,
    AgentLoopSynthesis,
    AgentLoopVerification,
    AgentLoopVerifyReplan,
    AgentLoopInit,
    AgentLoopMemoryRead,
    AgentLoopMemoryWrite,
    AgentLoopHygiene,
    AgentLoopKnowledge,
    AgentLoopMemoryCommands,
    AgentLoopRepair,
    AgentLoopSensor,
):
    """Runs one agent cycle: LLM planner picks tools, Executor runs them, Synthesizer answers."""

    #: Навешивается снаружи и лениво (`cli/commands_approval.py`).
    approval_inbox: ApprovalInbox | None = None

    # `__init__` приходит по MRO из `core/loop_init.py`.

    # ---------- public entry point ----------

    def run(
        self,
        user_question: str,
        file_hint: str | None = None,
        on_token: Any = None,
        deep_escalation: Any = None,
        task_id: str | None = None,
    ) -> str:
        """Run one observe→plan→act→verify→respond cycle.

        on_token: optional ``(str) -> None`` per-token streaming callback.
        deep_escalation: an operator ``OperatorEscalation`` — the only way to the deep tier.
        task_id: id of the logical task; survives a retry, unlike the run id.
        """
        with run_scope(new_id("run"), task_id) as _ctx:
            # Ребро происхождения — ПЕРВЫМ событием: иначе не сказать, чей это журнал.
            _trace_id = str(getattr(self.log, "trace_id", "") or "")
            if _trace_id:
                self.log.log("run_identity", identity_provenance(
                    trace_id=_trace_id,
                    run_id=_ctx.run_id,
                    task_id=_ctx.task_id,
                    session_id=getattr(self.memory, "session_id", None),
                ))
            else:
                # Полуребро запрещено, но молчать нельзя: иначе «связи нет» и
                # «связь не записали» неразличимы.
                self.log.log("run_identity_unavailable",
                             {"run_id": _ctx.run_id, "missing": "trace_id"})
            # Журнал потока данных наружу — новый на каждый ход.
            self.policy.egress = EgressLedger(user_question)
            try:
                return self._run_inner(
                    user_question=user_question,
                    file_hint=file_hint,
                    on_token=on_token,
                    deep_escalation=deep_escalation,
                )
            except (KeyboardInterrupt, CancelledError):
                # Cancellation is a control signal: record it and re-raise. Named
                # explicitly so SystemExit/MemoryError are not reinterpreted.
                self._record_aborted_episode(user_question, reason="cancelled")
                raise
            except Exception as exc:
                self._record_aborted_episode(
                    user_question, reason=type(exc).__name__
                )
                raise

    # Minimum quality for serving a stored answer instead of running a cycle.
    _REPLAY_MIN_QUALITY = 0.70
    # Jaccard overlap below which the stored question is not the same ask.
    _REPLAY_MIN_SIMILARITY = 0.85

    @staticmethod
    def _fast_path_allows_replay(episode: Any, similarity: float) -> bool:
        """Episode half of the fast-path gate; a `lesson` is never replayed as an answer."""
        return bool(
            episode is not None
            and similarity >= AgentLoop._REPLAY_MIN_SIMILARITY
            and effective_completion(episode) == "achieved"
            and AgentLoop._quality_allows_replay(episode)
            and getattr(episode, "full_answer", "")
            and not getattr(episode, "tools_used", ())
        )

    @staticmethod
    def _quality_allows_replay(episode: Any) -> bool:
        """May this episode's answer be replayed, on quality grounds alone?"""
        score = getattr(episode, "answer_quality_score", None)
        if score is None:
            return False
        return score >= AgentLoop._REPLAY_MIN_QUALITY

    def _run_inner(
        self,
        user_question: str,
        file_hint: str | None = None,
        on_token: Any = None,
        deep_escalation: Any = None,
    ) -> str:
        """The cycle body. Always entered through `run`, which owns run identity."""
        # On self so _synthesize() gets it without a signature change.
        self._stream_on_token = on_token
        self._cycle_findings = []
        # Tools that ACTUALLY ran, in order: procedure attribution (MIR-049) is
        # judged from this, not from the plan.
        self._executed_tools = []
        # Sensor verdicts about this run ITSELF; reset per cycle, or an inherited
        # fault is banked against the wrong episode.
        self._defect_signals = []
        self._self_defects_block = ""
        # Per-cycle, or a turn whose synthesis broke early inherits the
        # previous turn's contract verdict.
        self._synthesis_expects_contract_headers = True
        self.last_replan_exhausted = self.last_answer_was_clarification = False
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None
        # Experience-memory sinks are gated inside `_record_experience_memory`.
        may_knowledge = not self._durable_learning_suppressed("knowledge")
        may_source_registry = not self._durable_learning_suppressed("source_registry")
        may_profile = not self._durable_learning_suppressed("profile")
        may_assumptions = not self._durable_learning_suppressed("assumptions")

        user_question, _resumed = self._resume_clarification(user_question)

        _run_assumptions, _cp = self._open_run(user_question)

        # 1. Observe
        goal, _task_planner_llm, _task_synth_llm = self._observe_and_route(
            user_question,
            file_hint=file_hint,
            deep_escalation=deep_escalation,
            _cp=_cp,
        )

        # 2a. Completion contract (MIR-067), derived before any tool runs so the work
        # cannot shape its criterion. A LOCAL, never an attribute: it must not outlive the run.
        completion_contract = attach_checklist(
            self, derive_completion_contract(user_question, file_hint=file_hint), user_question
        )
        self.log.log(
            "completion_contract", completion_contract.to_log_payload()
        )

        _decided = self._odd_gate(user_question)
        if _decided is not None:
            return _decided

        # Never re-gated on a resumed run: the trigger lives in the ORIGINAL
        # text, which no longer changes, so asking again can only ask forever.
        _decided = None if _resumed else (self._prior_step_gate(user_question) or self._contract_ambiguity_gate(completion_contract) or self._clarification_gate(user_question))
        if _decided is not None:
            self._park_clarification(user_question)  # what it asked ABOUT
            return _decided

        # Memory retrieval — read-only injection into prompts
        (
            history,
            local_critique_active,
            persistent_block,
            experience_block,
            spend_block,
        ) = self._retrieve_turn_context(user_question, file_hint=file_hint)

        _decided = self._episodic_fast_path(
            user_question,
            file_hint=file_hint,
            goal=goal,
            local_critique_active=local_critique_active,
        )
        if _decided is not None:
            return _decided

        # Planner sees long-term memory so it can skip redundant tool calls. Role
        # stays out of `history`: `<conversation_history>` is only real prior dialogue.
        planner_history = "\n\n".join(
            p for p in (persistent_block, experience_block, spend_block, history) if p.strip()
        )
        multi_file = prepare_multi_file_review(
            user_question,
            file_hint=file_hint,
            workspace_root=self._file_read_workspace_root(),
            log=self.log.log,
        )
        # Ворота возвращают ответ, если решили ход, иначе None; выходит вызывающий.
        _decided = self._multi_file_refusal(
            multi_file, user_question=user_question, file_hint=file_hint, goal=goal,
        )
        if _decided is not None:
            return _decided
        forced_sources = (
            list(multi_file["sources"])
            if multi_file["kind"] == "forced"
            else None
        )
        forced_reasoning = str(multi_file.get("reasoning") or "")
        forced_warnings = list(multi_file.get("warnings") or [])

        # 3. Plan + 4. Act + 5. Observe Result + 6. Verify, in a bounded
        # re-planning loop (`_run_attempt_loop`).
        failure_history: list[ReplanTrigger] = []
        artifacts: dict[str, dict[str, Any]] = {}
        # Surfaces looping planners across attempts (MAST FM-1.3).
        self._step_repetition = StepRepetitionTracker()
        # Per-run termination guard (MAST FM-1.5, FM-3.1).
        self._termination_guard = TerminationGuard()
        # Typed Evidence chain, built alongside `artifacts` for synthesizer and Verifier.
        chain: ProvenanceChain = ProvenanceChain()
        planner_out: PlannerOutput | None = None
        plan: Plan | None = None
        replan_exhausted = False
        # S2 shadow: stagnation, reported at the end of the run; never stops anything.
        _stagnation_shadow: dict[str, Any] | None = None
        # S5 shadow: every disagreement seen this run, for the same purpose.
        _disagreement_shadow: list[dict[str, Any]] = []
        # True only for a trivial no-tool turn: trims synthesis context, forces the
        # LIGHT tier, skips the knowledge pipeline and memory consolidation.
        cheap_path_active = False
        # From the previous attempt's policy.decide(); empty on the first attempt.
        advice_for_planner: str = ""
        forbidden_actions: tuple[tuple[str, str], ...] = ()

        attempt = 0
        _attempt_state = AttemptState(
            user_question=user_question,
            file_hint=file_hint,
            goal=goal,
            planner_history=planner_history,
            failure_history=failure_history,
            local_critique_active=local_critique_active,
            forced_sources=forced_sources,
            forced_reasoning=forced_reasoning,
            forced_warnings=forced_warnings,
            _task_planner_llm=_task_planner_llm,
            _run_assumptions=_run_assumptions,
            _cp=_cp,
            advice_for_planner=advice_for_planner,
            forbidden_actions=forbidden_actions,
            attempt=attempt,
            artifacts=artifacts,
            chain=chain,
            planner_out=planner_out,
            plan=plan,
            replan_exhausted=replan_exhausted,
            cheap_path_active=cheap_path_active,
            _stagnation_shadow=_stagnation_shadow,
        )
        self._run_attempt_loop(_attempt_state)
        artifacts = _attempt_state.artifacts
        chain = _attempt_state.chain
        planner_out = _attempt_state.planner_out
        plan = _attempt_state.plan
        attempt = _attempt_state.attempt
        replan_exhausted = _attempt_state.replan_exhausted
        cheap_path_active = _attempt_state.cheap_path_active
        _stagnation_shadow = _attempt_state._stagnation_shadow

        # planner_out and plan are guaranteed set here (the for loop ran at
        # least once because max_replan_attempts >= 1 is enforced in __init__).
        assert planner_out is not None and plan is not None  # noqa: S101 — type narrowing, guarded above

        # Tool evidence was added per step; fold in subagent, sensor, memory and
        # user-directive evidence so the Verifier sees one uniform chain.
        self._fold_subagent_evidence(chain)
        self._fold_sensor_evidence(chain, spend_block)
        self._fold_evidence_chain(chain, persistent_block=persistent_block)

        # Первое значение — теневой вердикт: едет в событие ниже, здесь ничего не решает.
        (
            _premature_keyword_fired,
            source_ranking,
            source_registry,
        ) = self._rank_and_catalog_evidence(
            chain,
            user_question=user_question,
            artifacts=artifacts,
            cheap_path_active=cheap_path_active,
            may_knowledge=may_knowledge,
            may_source_registry=may_source_registry,
        )

        # 7. Respond. Even on replan exhaustion the synthesizer writes an Output
        # Contract reply, explaining honestly what was tried.
        # Current-run assumptions reach _synthesize via the instance.
        self._run_assumptions_current = _run_assumptions
        _synth_state = SynthesisState(
            goal=goal,
            user_question=user_question,
            file_hint=file_hint,
            artifacts=artifacts,
            planner_out=planner_out,
            plan=plan,
            history=history,
            persistent_block=persistent_block,
            spend_block=spend_block,
            failure_history=failure_history,
            replan_exhausted=replan_exhausted,
            cheap_path_active=cheap_path_active,
            local_critique_active=local_critique_active,
            _task_synth_llm=_task_synth_llm,
            _cp=_cp, chain=chain,
        )
        self._run_synthesizer_ladder(_synth_state)
        draft_answer = _synth_state.draft_answer
        _declared = _synth_state._declared

        # 7.5 Verifier: marks claims [verified:…] / [unverified]. A [web:URL] cite with
        # no matching evidence replans to fetch it, then re-verifies the same draft.
        _verify_state = VerifyState(
            draft_answer=draft_answer,
            user_question=user_question,
            file_hint=file_hint,
            goal=goal,
            chain=chain,
            artifacts=artifacts,
            attempt=attempt,
            plan=plan,
            planner_history=planner_history,
            failure_history=failure_history,
            source_ranking=source_ranking,
            source_registry=source_registry,
            may_knowledge=may_knowledge,
            may_source_registry=may_source_registry,
            _task_planner_llm=_task_planner_llm,
            _disagreement_shadow=_disagreement_shadow,
            _cp=_cp,
            planner_out=planner_out,
            replan_exhausted=replan_exhausted,
        )
        self._verify_and_settle_answer(_verify_state)
        answer = _verify_state.answer
        planner_out = _verify_state.planner_out
        replan_exhausted = _verify_state.replan_exhausted
        verifier_failure = _verify_state.verifier_failure

        # From here the response is a DRAFT: deciders rewrite claims (`set_body`) or
        # attach notices (`add_notice`); composition happens once, at `render()`.
        draft = self._build_response_draft(
            answer,
            user_question=user_question,
            artifacts=artifacts,
            replan_exhausted=replan_exhausted,
            local_critique_active=local_critique_active,
            verifier_failure=verifier_failure,
            completion_contract=completion_contract,
            failure_history=failure_history,
        )

        # ── Compose ─────────────────────────────────────────────────────────
        # The single arbitration point: no decider outranks another by running later;
        # the journal ledger shows any contribution that did not survive.
        answer = draft.render()
        self.log.log("response_composed", draft.to_log_payload(answer))

        # Strip internal verification markers before user-facing output.
        # Must happen AFTER output_policy which needs [verified:...] markers.
        answer = self._honor_requested_format(_strip_verification_markers(answer), user_question, draft_answer, _task_synth_llm)

        # Наблюдательно: вердикт в журнал, ход не меняется.
        self._check_completion_obligations(
            answer,
            user_question=user_question,
            file_hint=file_hint,
            artifacts=artifacts,
            chain=chain,
            plan=plan,
            failure_history=failure_history,
            completion_contract=completion_contract,
            _premature_keyword_fired=_premature_keyword_fired,
        )

        # Defence-in-depth: redact once more on the way out, so a hallucinated
        # credential or PII cannot bypass the kernel; the episode gets the same text.
        answer, verification, weak_chunks = self._finalize_run_tail(
            answer,
            user_question=user_question,
            artifacts=artifacts,
            planner_out=planner_out,
            replan_exhausted=replan_exhausted,
            may_profile=may_profile,
            may_assumptions=may_assumptions,
            _run_assumptions=_run_assumptions,
            _stagnation_shadow=_stagnation_shadow,
            _disagreement_shadow=_disagreement_shadow,
            _cp=_cp,
        )

        # ── Bank the episode LAST ────────────────────────────────────────────
        # An early `success` could not be corrected later (idempotency by run_id).
        # No outer permission gate: each of the three sinks is gated inside.
        self._record_experience_memory(
            goal_description=goal.description,
            question=user_question,
            answer=answer,
            tools_used=episode_tools(planner_out.sources, self._executed_tools),
            source_labels=list(artifacts.keys()) or ["general-knowledge"],
            verified_chunks=verification.verified_chunks if verification else 0,
            unverified_chunks=verification.unverified_chunks if verification else 0,
            weak_chunks=weak_chunks,
            replan_exhausted=replan_exhausted,
            # Set by either verifier soft-fail site (initial or replan).
            verifier_failure=verifier_failure,
            # Run-local: the verdict of the synthesis attempt that produced
            # THIS answer, or None when the ladder degraded.
            declared_completion=_declared["value"],
        )

        # Clear streaming callback so it cannot leak into the next turn.
        self._stream_on_token = None
        self.last_replan_exhausted = bool(replan_exhausted)
        return answer

    def _file_read_workspace_root(self) -> Path | None:
        """Resolved workspace root of the `file_read` tool, or None."""
        try:
            tool = self.registry.get("file_read")
        except KeyError:
            return None
        root = getattr(tool, "workspace_root", None)
        if root is None:
            return None
        return Path(root).resolve()
