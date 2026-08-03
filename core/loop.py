"""Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond.

This is the §3 cycle from the architecture, minimal but real:
  - Every phase produces a typed data model.
  - Every phase emits a structured log line.
  - Every action passes through the Policy Gate before execution.
  - A Plan may now contain multiple steps (file_read + web_search + ...).
  - Each artifact is labelled with its source so the Output Contract
    can cite it back to the user.

MVP-8 — Re-planning. The plan→execute→verify pipeline is wrapped in a
bounded retry loop. When every step in a plan fails (and the plan is
non-empty), the agent asks the planner for a NEW plan and shows it
exactly what went wrong via a `<replan_context>` block. Up to
`max_replan_attempts` total attempts; after that the cycle stops with
`error.code=replan_exhausted` and still produces an honest Output
Contract response so the user gets a real answer instead of a stack
trace.
"""
from __future__ import annotations

from asyncio import CancelledError
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.approval import ApprovalProvider
from core.data_classifier import DataClass, classify
from core.evidence import (
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_prior_turn,
    evidence_from_tool_result,
    make_evidence,
)
from core.file_request_intent import (
    force_file_hint_read_when_explicit,
    prepare_multi_file_review,
)
from core.ids import new_id
from core.llm import LLM
from core.logger import TraceLogger
from core.memory import WorkingMemory
from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWritePolicy,
)
from core.replan import ReplanTrigger, count_failures, format_replan_context
from core.run_context import run_scope

if TYPE_CHECKING:
    from core.approval_inbox import ApprovalInbox
    from core.clarification_policy import ClarificationResult
    from core.memory_echo_antibody import MemoryWriteRegistry
    from core.operational_domain import DomainResult
    from core.replan import ReplanPolicy
from core.actuation_gateway import GatewayPath
from core.assumption_registry import (  # Layer 5
    AssumptionRegistry,
    AssumptionStore,
    extract_from_plan,
    extract_from_question,
)
from core.completion_contract import derive_completion_contract
from core.completion_marker import (
    marker_instruction as completion_marker_instruction,
)
from core.completion_marker import (
    new_nonce as new_completion_nonce,
)
from core.completion_marker import (
    parse_completion_marker,
)
from core.completion_obligation import evaluate_completion_obligations
from core.evidence_classes import (
    SelfAnalysisDecision,
    is_self_analysis_turn,
)
from core.evidence_support import evaluate_evidence_support
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.loop_helpers import (  # noqa: F401 -- re-exported
    _ANSWER_CITATION_RE,
    _VERIF_MARKER_RE,
    DEFAULT_MAX_REPLAN_ATTEMPTS,
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    _strip_verification_markers,
    _to_text,
    citation_for_evidence,
    file_scope_notice,
    format_allowed_citations_block,
    format_artifact,
    format_human_response,
    new_trace_id,
    output_contract_requires_headers,
    untrusted_scan_view,
)
from core.loop_methods import AgentLoopExtractedMethods
from core.loop_methods2 import (
    AgentLoopExtractedMethods2,
)
from core.loop_step_execution import (
    _TOOL_SOURCE_HINTS as _TOOL_SOURCE_HINTS,
)
from core.loop_step_execution import (
    _TRUSTED_INTERNAL_TOOLS as _TRUSTED_INTERNAL_TOOLS,
)
from core.loop_step_execution import (
    AgentLoopStepExecution,
)
from core.loop_step_execution import (
    _step_trigger_tls as _step_trigger_tls,
)
from core.low_evidence_policy import (
    is_evidence_expected,
)
from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelBudgetExceeded
from core.models import (
    ErrorObject,
    Goal,
    Observation,
    Plan,
    PlanStep,
)
from core.output_policy import apply_ranker_output_policy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner, PlannerOutput
from core.policy import PolicyGate
from core.reasoning_action_check import check_reasoning_actions
from core.redaction import (
    collect_pii_findings,
    redact_dlp_text,
    scan,
)
from core.referent_resolver import (
    FileHintRef,
    PriorTurnRef,
    ReferentDecision,
    ReferentResolver,
    artifacts_from_working_memory,
    citation_token_for_referent,
    is_local_critique_eligible,
    is_show_only_directive,
    referent_resolver_mode,
)

# Шаг плана уехал в свой модуль (правило «компактные модули»); имена
# ре-экспортируются, чтобы существующие импорт-пути не порвались.
# `ReplanCode` (алиас FailureType) жил здесь и импортируется снаружи
# (tests/test_replan_audit.py). Исполнение шага уехало и унесло его
# использование — сохраняем шов явным ре-экспортом.
from core.replan import FailureType as ReplanCode  # noqa: F401 — шов импорта
from core.response_draft import ResponseDraft
from core.role_router import RoleContext, RoleRouter
from core.smart_memory import (
    _COMPLETION_DECLARATIONS,
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    effective_completion,
)
from core.source_ranker import SourceRankingReport, rank_chain
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from core.step_repetition import StepRepetitionTracker
from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    run_synthesizer_ladder,
)
from core.task_complexity import can_skip_planner
from core.termination_guard import TerminationGuard
from core.unsupported_claims import apply_answer_enforcement
from core.user_profile import UserProfile, UserProfileStore, profile_to_prompt_block
from core.verification_summary import build_verification_summary
from tools.base import ToolRegistry

# Default attempt budget for re-planning. Two replans (3 attempts total)
# is the tradeoff: enough room to recover from a typo or a flaky source,
# not enough to mask a fundamentally wrong plan as "just one more try".


# ReplanCode is an alias for FailureType (core/replan.py) — single source of truth.
# Imported above. No local definition needed. `ReplanTrigger` and the two
# helpers that summarise/format the failure history moved there too
# (2026-08-02): the whole replan vocabulary now lives in one module.


# Output Contract (§1 Interface & Communication + §8 Verification).
# The LLM MUST emit this structure so the user gets:
#   - a direct answer
#   - explicit citations to source labels embedded in the evidence
#   - explicit confidence and unverified gaps

# §3.x — register this prompt with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("synthesizer.system", SYSTEM_ANSWER, module="core.loop",
        description="Output contract for the LLM synthesizer (§3 Cognitive Core)")
except ImportError:  # pragma: no cover
    pass

# Regex that matches the internal verification markers the Verifier inlines
# into the answer text.  These are audit annotations, not user content.
# Stripped before the answer leaves the kernel so users never see them.


# Regex that strips source citation tokens from individual sentences/bullets.
# Matches: [general-knowledge] [web:url] [file:path] [search:q] [test:cmd]
# [log:id] [shell:cmd] [diff:p] [memory:id] [user] [declared:...] etc.


class AgentLoop(
    AgentLoopStepExecution,
    AgentLoopExtractedMethods2,
    AgentLoopExtractedMethods,
):
    """Runs a single agent cycle.

    MVP-3: the planner is an LLM. The CLI only supplies the question plus an
    optional file hint. The planner picks which tools (if any) to call; the
    Executor runs the plan and the Synthesizer produces the Output Contract.
    """

    #: Навешивается снаружи и лениво: `cli/commands_approval.py` создаёт ящик
    #: при первом обращении. Контракт держался на `getattr` со строкой и был
    #: невидим; объявляем явно, значение по умолчанию прежнее.
    approval_inbox: ApprovalInbox | None = None

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyGate,
        llm: LLM,
        logger: TraceLogger,
        planner: LLMPlanner | None = None,
        model_router: ModelRouter | None = None,
        memory: WorkingMemory | None = None,
        persistent_store: PersistentMemoryStore | None = None,
        retrieval_policy: MemoryRetrievalPolicy | None = None,
        write_policy: MemoryWritePolicy | None = None,
        memory_write_registry: MemoryWriteRegistry | None = None,
        role_router: RoleRouter | None = None,
        knowledge_use_policy: KnowledgeUsePolicy | None = None,
        source_registry_store: SourceRegistryStore | None = None,
        knowledge_pipeline: KnowledgePipeline | None = None,
        episodic_store: EpisodicMemoryStore | None = None,
        procedural_store: ProceduralMemoryStore | None = None,
        consolidation_store: MemoryConsolidationStore | None = None,
        knowledge_auto_write: bool = False,
        approval_provider: ApprovalProvider | None = None,
        max_replan_attempts: int = DEFAULT_MAX_REPLAN_ATTEMPTS,
        replan_policy: ReplanPolicy | None = None,
        verifier_enabled: bool = True,
        clarification_enabled: bool = True,
        clarification_gate_enabled: bool = True,
        odd_enabled: bool = True,
        cheap_path_enabled: bool = True,
        user_profile_store: UserProfileStore | None = None,
        assumption_store: AssumptionStore | None = None,  # Layer 5
        gateway_dry_run: bool = False,
        gateway_path: GatewayPath = "repl",
        experience_retrieval: bool = True,
        episodic_replay: bool = True,
        durable_writes: frozenset[str] | None = None,
    ):
        self.registry = registry
        self.policy = policy
        self.gateway_dry_run = gateway_dry_run
        self.gateway_path = gateway_path
        # ── Memory permissions (INSTANCE-scoped, fixed for this agent's life) ──
        # Holding stores is not the same permission as reading them, replaying
        # an answer from them, or writing durable state. These three keep those
        # apart; they are set once at construction and there is deliberately no
        # per-run API yet.
        #   experience_retrieval — inject episodic/procedural memory into planning
        #   episodic_replay      — allow the fast path to serve a stored answer
        #   durable_writes       — which durable sinks this agent may write.
        #                          None = all (interactive default); a frozenset
        #                          is an ALLOWLIST and everything outside it is
        #                          denied, including unknown sink names. The
        #                          audit and dry-run brakes outrank it entirely
        #                          (see `_durable_learning_suppressed`).
        self.experience_retrieval = experience_retrieval
        self.episodic_replay = episodic_replay
        self.durable_writes = durable_writes
        self.suppress_durable_learning_writes = False
        # Audit / read-only execution brake. When True, the loop performs NO
        # durable learning writes (episodic, procedural, consolidation, user
        # profile, persistent access-stat bumps) and freezes 'agent-auto'
        # persistent/semantic writes on the shared write policy. This is a
        # deterministic operator control (see :audit) — set BEFORE planning so
        # an investigation of memory cannot silently contaminate the store it
        # is auditing. It is independent of `suppress_durable_learning_writes`
        # (which the dry-run path save/restores) so an autonomous cycle cannot
        # accidentally lift the audit brake mid-run.
        self.audit_read_only = False
        # Records whether *this* audit toggle installed the agent-auto freeze,
        # so turning audit off never lifts an AGENT_FREEZE_AUTO_MEMORY env brake.
        self._audit_froze_agent_auto = False
        self.model_router = model_router or ModelRouter.single(llm)
        self.llm = self.model_router.for_role(ModelRole.SYNTHESIZER)
        self.log = logger
        self.planner = planner or LLMPlanner(
            llm=self.model_router.for_role(ModelRole.PLANNER),
            registry=registry,
        )
        self.memory = memory  # may be None for stateless one-shot use
        self.persistent_store = persistent_store
        self.retrieval_policy = retrieval_policy or MemoryRetrievalPolicy()
        self.write_policy = write_policy or MemoryWritePolicy()
        self.memory_write_registry = memory_write_registry
        self.role_router = role_router or RoleRouter()
        self.knowledge_use_policy = knowledge_use_policy or KnowledgeUsePolicy()
        self.source_registry_store = source_registry_store
        self.knowledge_pipeline = knowledge_pipeline or KnowledgePipeline()
        self.episodic_store = episodic_store
        self.procedural_store = procedural_store
        self.consolidation_store = consolidation_store
        self.knowledge_auto_write = bool(knowledge_auto_write)
        # When None, escalated actions are blocked outright (safe default).
        self.approval_provider = approval_provider
        # MVP-12: replan policy decides per-FailureType budgets, advises
        # the planner, and tracks forbidden (tool, args) pairs that must
        # not be retried after approval_deny / policy_blocked etc.
        #
        # Backward compatibility: the older `max_replan_attempts` parameter
        # is still honoured. If the caller supplied an explicit policy we
        # use it as-is; otherwise we synthesise the default policy with the
        # caller's global cap. Mismatch is an error — we refuse to silently
        # ignore one of the two knobs.
        if max_replan_attempts < 1:
            raise ValueError(
                f"max_replan_attempts must be >= 1, got {max_replan_attempts}"
            )
        from core.replan import (
            ReplanPolicy as _ReplanPolicyCls,
        )

        if replan_policy is None:
            self.replan_policy = _ReplanPolicyCls(
                max_total_replans=max_replan_attempts
            )
        else:
            if (
                max_replan_attempts != DEFAULT_MAX_REPLAN_ATTEMPTS
                and max_replan_attempts != replan_policy.max_total_replans
            ):
                raise ValueError(
                    "Pass either `max_replan_attempts` OR `replan_policy`, "
                    "not both with conflicting caps "
                    f"(max_replan_attempts={max_replan_attempts}, "
                    f"replan_policy.max_total_replans="
                    f"{replan_policy.max_total_replans})"
                )
            self.replan_policy = replan_policy
        # Keep the legacy attribute for any external code reading it.
        self.max_replan_attempts = self.replan_policy.max_total_replans
        # MVP-11 Compensation registry. Every successful tool call that
        # carries a non-noop CompensationPlan in its structured output
        # gets appended here, LIFO. `rollback()` pops the last plan and
        # applies it via `core.compensation.apply_compensation_plan`.
        # Persistence is intentionally in-memory: rollback must be done
        # WITHIN the session that produced the change (a future MVP can
        # serialise the log if cross-session undo proves useful).
        from core.compensation import CompensationPlan  # avoid top-level cycle

        self._CompensationPlanCls = CompensationPlan
        self.compensation_log: list[CompensationPlan] = []
        # Per-cycle scratch list, populated by `_execute_step` whenever a
        # secret is found in a tool output. `run()` resets it on entry and
        # passes it into the synthesizer so the answer can mention any
        # redaction action it took.
        self._cycle_findings: list[dict[str, Any]] | None = None
        # Per-step scratch slot. `_execute_step` writes a `ReplanTrigger`
        # into this every time it bails out with `None`. The parent loop
        # in `run()` consumes it and clears it. Keeping it as instance
        # state mirrors the `_cycle_findings` pattern and avoids
        # restructuring the return type of `_execute_step`.
        self._last_step_failure: ReplanTrigger | None = None
        # Current attempt number — set by `run()` at the top of each
        # iteration so `_execute_step` can stamp failure triggers with
        # the right attempt index without taking a new parameter.
        self._current_attempt: int = 1
        # Synthesis streaming callback — set by `run()` when the caller
        # passes `on_token`; read by `_synthesize()`. Reset to None at
        # the end of each cycle so leaking across turns is impossible.
        self._stream_on_token: Any = None
        # Set by the synthesizer resilience ladder when every synthesis attempt
        # failed and the turn fell back to an honest degraded answer.
        self._last_synth_degraded: bool = False
        # MVP-14.1 — provenance scratch state. `_retrieve_persistent`
        # stashes the records it injected so `run()` can fold them into
        # the Evidence chain. `last_provenance` is set at the end of
        # each `run()` and exposed for tests / the upcoming Verifier.
        self._last_persistent_records: list[Any] = []
        self._last_episode_records: list[Any] = []
        self._last_procedure_records: list[Any] = []
        # Fast-path cache: best episodic match from the last _retrieve_experience_memory call.
        # Keyed so run() can serve a cached answer without touching the LLM.
        self._last_best_similar_episode: Any = None  # EpisodeRecord | None
        self._last_best_similar_score: float = 0.0
        # Planner plan cache: skips LLM planner call for identical questions
        # within the same session (invalidated when the episodic store changes).
        # Key: (hash(question), episodic_mtime, file_hint)
        self._planner_cache: dict[tuple[int, float, str], Any] = {}
        # MAST FM-1.3 step-repetition tracker; replaced per `run()` call.
        self._step_repetition: StepRepetitionTracker = StepRepetitionTracker()
        # MAST FM-1.5 / FM-3.1 termination guard; replaced per `run()` call.
        self._termination_guard: TerminationGuard = TerminationGuard()
        self.last_provenance: ProvenanceChain = ProvenanceChain()
        self.last_role_context: RoleContext = self.role_router.route("")
        # MVP-14.4 — Verifier wiring. `verifier_enabled=False` skips the
        # annotation pass; the draft answer is returned verbatim. Useful
        # for legacy tests and for callers who want raw LLM output.
        self.verifier_enabled = bool(verifier_enabled)
        # Layer 4 — User Profile store and last-known profile snapshot.
        self.user_profile_store = user_profile_store
        self.last_user_profile: UserProfile | None = None
        # Layer 5 — Assumption Registry store and last-run snapshot.
        self.assumption_store = assumption_store
        self.last_assumptions: AssumptionRegistry | None = None
        self._run_assumptions_current: AssumptionRegistry | None = None
        # §3 Clarification Policy wiring. `clarification_enabled=False` skips
        # the heuristic ambiguity check. Intended ONLY for integration tests
        # that exercise the tool/approval layer with synthetic short questions.
        self.clarification_enabled = bool(clarification_enabled)
        # B-1 Clarification Gate wiring (режим переспроса). When the loop
        # gets STUCK (replan exhausted == loop_suspected), switch to
        # "ask, don't build": prepend the minimal clarifying questions to the
        # honest failure answer so the operator can narrow the frame instead of
        # the agent churning. `False` skips it for tests that assert on the bare
        # exhaustion answer. Pure/deterministic — no LLM, no I/O.
        self.clarification_gate_enabled = bool(clarification_gate_enabled)
        # TD-032 slice 3 — last run stuck signal for autonomous runtime parity.
        self.last_replan_exhausted: bool = False
        # §7 Operational Design Domain (ODD / B-05) wiring. When enabled, an
        # out-of-domain request is refused or escalated BEFORE any planning.
        # `odd_enabled=False` skips the check for integration tests.
        self.odd_enabled = bool(odd_enabled)
        # Cheap-path planner gate (TD — trivial no-tool input skips the planner
        # LLM call and synthesises directly). `False` forces the planner to run
        # for every input, preserving the pre-gate behaviour for tests/rollback.
        self.cheap_path_enabled = bool(cheap_path_enabled)
        from core.verifier import VerificationReport as _VR

        self.last_verification: _VR | None = None
        self.last_referent_decision: ReferentDecision | None = None
        # Issue #119 — was this turn a conversational correction / request to
        # explain the agent's own previous reply? Decided per turn, exposed so
        # tests and operators can see why dialogue evidence was (not) admitted.
        self.last_self_analysis: SelfAnalysisDecision | None = None
        # MVP-14.3/14.3x — trust metadata over the Evidence chain.
        # Source ranking is logged/exposed, and Ranker-to-Output Policy uses
        # it to cap confidence for unsuitable realtime sources.
        self.last_source_ranking: SourceRankingReport | None = None
        # Source registry is the catalog view over the same chain:
        # source records plus first-pass claims extracted from Evidence.
        self.last_source_registry: SourceRegistry = SourceRegistry()
        self.last_knowledge_pipeline: KnowledgePipelineResult | None = None

    # ---------- audit / read-only execution brake ----------


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

        Args:
            user_question: The user's natural-language input.
            file_hint: Optional workspace file path to pre-load.
            on_token: Optional ``(str) -> None`` callback invoked for each
                      synthesis token as it streams from the LLM.  Pass
                      ``lambda t: print(t, end="", flush=True)`` for live
                      CLI display.  ``None`` (default) disables streaming.
            deep_escalation: Optional operator-supplied
                      :class:`~core.deep_escalation.OperatorEscalation`. Only an
                      explicit, valid operator reason lets planner/synthesizer
                      escalate to the deep (Opus) tier; the default ``None``
                      keeps every autonomous run on the standard tier.
            task_id: Optional id of the *logical task* this run serves. It
                      survives a retry; the run id minted below does not.

        This is a thin wrapper: it owns run identity and nothing else, so the
        identity is bound before any cycle work and released even if the cycle
        raises. The body lives in `_run_inner`.
        """
        with run_scope(new_id("run"), task_id):
            try:
                return self._run_inner(
                    user_question=user_question,
                    file_hint=file_hint,
                    on_token=on_token,
                    deep_escalation=deep_escalation,
                )
            except (KeyboardInterrupt, CancelledError):
                # Cancellation is a control signal, not a failure to absorb.
                # Record the outcome honestly, then let it keep propagating —
                # swallowing it would strand the caller that asked to stop.
                # Caught explicitly rather than via `except BaseException` so
                # unrelated exits (SystemExit, MemoryError) are not reinterpreted.
                self._record_aborted_episode(user_question, reason="cancelled")
                raise
            except Exception as exc:
                self._record_aborted_episode(
                    user_question, reason=type(exc).__name__
                )
                raise

    # Minimum measured quality an episode needs before its answer may be
    # served verbatim instead of running a real cycle.
    _REPLAY_MIN_QUALITY = 0.70
    # Jaccard overlap with the stored question below which a replay is not
    # even considered the same ask.
    _REPLAY_MIN_SIMILARITY = 0.85

    @staticmethod
    def _fast_path_allows_replay(episode: Any, similarity: float) -> bool:
        """The episode-shaped half of the fast-path gate.

        Named so the three episodic readers agree on how the completion axis
        is read: through the frozen state and the shared accessor, never the
        declaration and never a re-derivation. Replay serves a stored answer
        INSTEAD of running a cycle, so a `lesson` gets no exception here —
        being retrievable as a warning is not being reusable as an answer.
        """
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
        """May this episode's answer be replayed, on quality grounds alone?

        An unmeasured score (None — the episode carried no evidence chunks)
        is refused. Absence of measurement is not evidence of quality, and
        the previous encoding of "unmeasured" as 1.0 cleared this gate by the
        widest possible margin (MIR-002).
        """
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
        # Store streaming callback so _synthesize() can pick it up without
        # changing its signature (which is called from multiple paths).
        self._stream_on_token = on_token
        self._cycle_findings = []
        # Tools that ACTUALLY executed this run, in order. Procedure
        # attribution (MIR-049) is judged from this rather than from the plan,
        # so a run cancelled before reaching a procedure's steps never debits
        # it. Accumulated as execution happens so an exception cannot discard
        # attribution already earned.
        self._executed_tools = []
        # Sensor verdicts this run raised about ITSELF, accumulated as they
        # fire. Each of these sensors used to log and drop its finding, so a
        # run's own faults never reached the episode and the same mistake could
        # be repeated indefinitely without a trace. Reset per cycle for the same
        # reason `_executed_tools` is: instance state outlives a run, and an
        # inherited fault would be banked against the wrong episode.
        self._defect_signals = []
        self.last_replan_exhausted = False
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None
        # Per-sink permissions for this cycle. Experience-memory sinks
        # (episode/procedure/consolidation) are resolved inside
        # `_record_experience_memory`, which owns those three writes.
        may_knowledge = not self._durable_learning_suppressed("knowledge")
        may_source_registry = not self._durable_learning_suppressed("source_registry")
        may_profile = not self._durable_learning_suppressed("profile")
        may_assumptions = not self._durable_learning_suppressed("assumptions")

        # Layer 4 — load the user profile for this cycle.
        if self.user_profile_store is not None:
            self.last_user_profile = self.user_profile_store.load_or_default()
            self.log.log(
                "user_profile_load",
                {
                    "expertise": self.last_user_profile.expertise,
                    "verbosity": self.last_user_profile.verbosity,
                    "language": self.last_user_profile.language,
                    "interaction_count": self.last_user_profile.interaction_count,
                    "interests": self.last_user_profile.interests,
                },
            )

        # Layer 5 — create a fresh AssumptionRegistry and seed it from the question.
        # Layer 4→5 bridge: pass the profile's known language so extract_from_question
        # uses a higher-confidence profile signal instead of a raw heuristic.
        _run_assumptions = AssumptionRegistry(
            run_id=getattr(self.log, "trace_id", ""),
        )
        # MIR-027: the store is an ARCHIVE, not an active input. The cross-
        # turn auto-restore that sat here leaked assumptions between unrelated
        # goals (session-lifetime trace id) and served no other caller;
        # archived rows return only via explicit retrieval. Measurements and
        # the operator's ruling live in the MIR-027 registry entry.
        try:
            _known_lang: str | None = None
            if self.last_user_profile is not None:
                _known_lang = self.last_user_profile.language or None
            _q_assumptions = extract_from_question(
                user_question,
                run_id=getattr(self.log, "trace_id", ""),
                known_language=_known_lang,
            )
            _run_assumptions.register_many(_q_assumptions)
        except Exception:
            pass  # Assumption extraction must never abort the run.

        # §3.5 Checkpoint writer — one file per trace, append-only.
        # Falls back to a no-op sentinel when the logger is a test spy that
        # does not expose trace_id / log_dir (avoids coupling tests to I/O).
        try:
            from core.checkpoint import CheckpointWriter as _CPWriter
            _cp: Any = _CPWriter(trace_id=self.log.trace_id, log_dir=self.log.log_dir)
        except (AttributeError, ValueError):
            class _NoOpCP:
                """Silently drops all checkpoint calls."""
                def save_observe(self, **_kw: Any) -> None: pass
                def save_plan(self, **_kw: Any) -> None: pass
                def save_act(self, **_kw: Any) -> None: pass
                def save_respond(self, **_kw: Any) -> None: pass
                def save_paused(self, _data: dict[str, Any]) -> None: pass
            _cp = _NoOpCP()

        # 1. Observe
        observation = Observation(
            source="cli",
            modality="text",
            content={
                "question": user_question,
                "file_hint": file_hint,
            },
            provenance="user",
        )
        self.log.log("observe", observation)
        _cp.save_observe(question=user_question, file_hint=file_hint)

        # Classify the question itself. A user can paste a secret into the
        # prompt; the kernel must catch it BEFORE the LLM sees it.
        q_cls = classify(user_question, source="cli")
        self.log.log(
            "data_classified",
            {
                "label": "user_question",
                "class": q_cls.cls.value,
                "source": q_cls.source,
                "reasons": q_cls.reasons,
            },
        )
        if q_cls.cls == DataClass.SECRET:
            q_findings = scan(user_question)
            kinds = sorted({f.kind for f in q_findings})
            self.log.log(
                "secret_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )
        elif q_cls.cls == DataClass.SENSITIVE:
            q_findings = collect_pii_findings(user_question)
            kinds = sorted({f"pii-{f.kind}" for f in q_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )

        self.last_role_context = self.role_router.route(user_question)
        self.log.log("role_route", self.last_role_context.to_log_payload())

        # Adaptive model routing: choose LLM tier based on task complexity.
        # LIGHT tasks (greetings, quick lookups) get a cheap/fast model.
        # DEEP tasks (architecture, full audits, from-scratch builds) get
        # the most powerful available model (opus, o1, …).
        # STANDARD tasks reuse the default role route — no change.
        # The RoleRouter verdict computed just above is forwarded so a repair
        # or programming task can never be downgraded to the LIGHT tier on the
        # strength of a terse phrasing alone.
        #
        # Failure handling mirrors `_record_experience_memory`: a `TypeError`
        # here is a CALL-SIGNATURE DEFECT, not a routing fault -- `for_task()`
        # was invoked with an argument it does not accept (or without one it
        # requires). Laundering that into a silent fallback hid the defect
        # completely: every task would quietly answer on the default model with
        # nothing in the log to say routing had stopped working. So `TypeError`
        # PROPAGATES; every other failure keeps the default-LLM fallback but is
        # now recorded instead of vanishing.
        try:
            _task_role = getattr(self.last_role_context, "role", None)
            _task_planner_llm = self.model_router.for_task(
                ModelRole.PLANNER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _task_synth_llm = self.model_router.for_task(
                ModelRole.SYNTHESIZER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _planner_model = getattr(_task_planner_llm, "model", None)
            _synth_model = getattr(_task_synth_llm, "model", None)
            _route_reason = getattr(
                getattr(_task_planner_llm, "route", None), "reason", "default"
            )
            self.log.log(
                "adaptive_route",
                {
                    "question_chars": len(user_question),
                    "task_role": _task_role,
                    "planner_model": _planner_model,
                    "synth_model": _synth_model,
                    "route_reason": _route_reason,
                },
            )
        except TypeError:
            # Visible on purpose -- see the comment above.
            raise
        except Exception as exc:  # noqa: BLE001
            _task_planner_llm = None
            _task_synth_llm = None
            self.log.log(
                "adaptive_route_error",
                {
                    "error": type(exc).__name__,
                    "detail": str(exc)[:200],
                    "question_chars": len(user_question),
                    "fallback": "default_llm",
                },
            )

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

        # 2a. Completion contract (MIR-067) — derived from the REQUEST, here,
        # before a single tool runs. The ordering is the proof: this event
        # precedes every `act`/`tool_call` in the journal, so the criterion
        # cannot have been shaped by the work it judges. Recorded even when
        # empty, because "this request owed nothing verifiable" is itself the
        # fact a later reader needs.
        # Deliberately a LOCAL, never an attribute: a contract that outlived
        # its run would judge the NEXT request by this one's criterion.
        # `tests/test_completion_marker.py` pins that invariant for the whole
        # completion family, and it caught this exact mistake in review.
        completion_contract = derive_completion_contract(
            user_question, file_hint=file_hint
        )
        self.log.log(
            "completion_contract", completion_contract.to_log_payload()
        )

        # 2b. Operational Design Domain gate (§7 ODD / B-05).
        # Pure-heuristic check — no LLM, no I/O.  When the request falls
        # outside the agent's operational domain (harmful/illegal, real
        # money, physical world, regulated advice, authority over people),
        # the loop stops here BEFORE any planning and returns an honest
        # refusal/escalation message instead of improvising an action.
        if self.odd_enabled:
            _odd = self._check_operational_domain(user_question)
            if _odd.blocks:
                self.log.log(
                    "out_of_domain",
                    {
                        "verdict": _odd.verdict,
                        "action": _odd.action,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _odd.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _odd.message

        # 2c. Clarification Policy (§3 Clarification Policy).
        # Pure-heuristic check — no LLM, no I/O.  When the question is
        # ambiguous about a destructive action the loop stops here and
        # returns the clarification question to the caller so the REPL
        # can surface it before any planning starts.
        if self.clarification_enabled:
            _clarif = self._check_clarification(user_question)
            if _clarif.should_ask:
                self.log.log(
                    "clarification_request",
                    {
                        "question": _clarif.question,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _clarif.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _clarif.question

        # Memory retrieval — read-only injection into prompts
        history = ""
        if self.memory is not None:
            history = self.memory.conversation_context(max_turns=5)
            if history:
                self.log.log(
                    "memory_inject",
                    {
                        "session_id": self.memory.session_id,
                        "turns_visible": len(self.memory.recent_turns(5)),
                        "history_chars": len(history),
                        "artifacts_cached": len(self.memory.artifacts),
                    },
                )

        # Issue #119 — conversational correction / self-analysis classification.
        # Decided here, before planning, because it changes which evidence class
        # the answer is later judged against: a claim about THIS session's own
        # exchange is backed by the transcript, not by an external source.
        # Deterministic and always on (no feature flag): its only effect is to
        # admit dialogue evidence that the verifier then scopes narrowly, and a
        # bug the operator cannot report is worse than the risk of that.
        _self_analysis = is_self_analysis_turn(
            user_question,
            has_prior_turn=bool(
                self.memory is not None and self.memory.recent_turns(1)
            ),
        )
        self.last_self_analysis = _self_analysis
        if _self_analysis.is_self_analysis:
            self.log.log("self_analysis_turn", _self_analysis.to_log_payload())

        # Referent resolver (critique PR1/PR2) — shadow logs; on enables path.
        self._maybe_resolve_referent(user_question, file_hint=file_hint)
        local_critique_active = (
            referent_resolver_mode() == "on"
            and self.last_referent_decision is not None
            and is_local_critique_eligible(self.last_referent_decision)
        )
        if local_critique_active:
            _rd = self.last_referent_decision
            assert _rd is not None and _rd.primary is not None
            self.log.log(
                "local_critique_path",
                {
                    "status": _rd.status,
                    "kind": _rd.primary.kind,
                    "show_only": is_show_only_directive(_rd.directive_excerpt),
                    "target_chars": len(_rd.analysis_target_excerpt),
                    "citation": citation_token_for_referent(_rd),
                },
            )

        # Persistent memory retrieval — pick a few long-term records that
        # share keywords with the question, then format them as a
        # <long_term_memory> block injected into planner + synthesizer.
        # Local-critique turns suppress default LTM/episodic injection (PR2).
        if local_critique_active:
            persistent_block = ""
            experience_block = ""
            self._last_best_similar_episode = None
            self._last_best_similar_score = 0.0
        else:
            persistent_block = self._retrieve_persistent(user_question)
            experience_block = self._retrieve_experience_memory(user_question)

        # ── Episodic fast path ───────────────────────────────────────────────────
        # Jaccard ≥ 0.85 AND quality ≥ 0.70 → serve the stored answer directly,
        # skipping both the planner LLM call and the synthesizer LLM call.
        # Conditions that disable the fast path:
        #   - episodic_replay is False (this agent may read experience memory
        #     but may not serve a stored answer in place of a fresh cycle —
        #     the unattended profile runs this way)
        #   - file_hint is set (the answer is tied to a specific file)
        #   - question starts with ':' (operator command)
        #   - full_answer is empty (episode from before this feature)
        #   - the cached episode used tools — its answer depends on the state of
        #     the environment (files, installed packages, command output) which
        #     may have changed since; only purely reasoned answers (no tools)
        #     are safe to replay verbatim.
        #   - local_critique_active (must critique current referent, not replay)
        _fp_ep = self._last_best_similar_episode
        _fp_score = self._last_best_similar_score
        if (
            self.episodic_replay
            and not local_critique_active
            and not file_hint
            and not user_question.strip().startswith(":")
            and self._fast_path_allows_replay(_fp_ep, _fp_score)
        ):
            self.log.log(
                "episodic_fast_path",
                {
                    "episode_id": _fp_ep.id,
                    "similarity": round(_fp_score, 4),
                    "quality": round(_fp_ep.answer_quality_score, 4),
                    "answer_chars": len(_fp_ep.full_answer),
                },
            )
            # A replay produces NO new evidence: nothing was fetched, nothing
            # was verified this cycle. Banking it as verified_chunks=1 minted
            # verification out of "it matched something in memory", and the
            # replay then looked as trustworthy as the answer it copied — a
            # self-reinforcing chain (MIR-041).
            #
            # unverified=1 rather than 0/0 on purpose: an empty chain scores
            # quality 1.0 (MIR-002), which would hand the replay top marks for
            # having no evidence at all. The source episode is named in
            # source_labels so the copy stays traceable to its origin.
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=_fp_ep.full_answer,
                tools_used=[],
                source_labels=[f"memory:{_fp_ep.id}"],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # No verifier ran on a replay, so none of it crashed. The
                # distinction matters: this flag means "the verifier threw",
                # not "no verification happened".
                verifier_failure=False,
            )
            if self.memory is not None:
                self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="episodic fast path — cached answer",
                    tools_used=[],
                    artifact_labels=[f"memory:{_fp_ep.id}"],
                    answer=_fp_ep.full_answer,
                )
            self._stream_on_token = None
            return _fp_ep.full_answer

        # Planner sees the persistent block prepended to working history so
        # it can opt out of redundant tool calls when the answer is already
        # in long-term memory. Role is logged and injected into synthesis,
        # but kept out of `history` so `<conversation_history>` stays a
        # strict marker for actual prior dialogue.
        planner_history = "\n\n".join(
            part for part in (persistent_block, experience_block, history) if part.strip()
        )
        multi_file = prepare_multi_file_review(
            user_question,
            file_hint=file_hint,
            workspace_root=self._file_read_workspace_root(),
            log=self.log.log,
        )
        if multi_file["kind"] == "refusal":
            answer = str(multi_file["message"])
            self.log.log("multi_file_review_refused", multi_file)
            self.log.log(
                "respond",
                {
                    "chars": len(answer),
                    "sources": [f"file:{file_hint}"] if file_hint else [],
                    "redactions": len(self._cycle_findings or []),
                    "attempts_used": 0,
                    "replan_exhausted": False,
                },
            )
            if self.memory is not None:
                turn = self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="kernel multi-file review refusal",
                    tools_used=[],
                    artifact_labels=[],
                    answer=answer,
                )
                self.log.log(
                    "memory_write",
                    {
                        "session_id": self.memory.session_id,
                        "turn_id": turn.id,
                        "turn_index": turn.index,
                        "tools_used": turn.tools_used,
                        "labels": turn.artifact_labels,
                    },
                )
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=answer,
                tools_used=[],
                source_labels=[f"file:{file_hint}"] if file_hint else [],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # The refusal returns before planning, so verification never
                # started — again not a crash.
                verifier_failure=False,
            )
            self._stream_on_token = None
            return answer
        forced_sources = (
            list(multi_file["sources"])
            if multi_file["kind"] == "forced"
            else None
        )
        forced_reasoning = str(multi_file.get("reasoning") or "")
        forced_warnings = list(multi_file.get("warnings") or [])

        # 3. Plan + 4. Act + 5. Observe Result + 6. Verify, wrapped in a
        # bounded re-planning loop. On every iteration:
        #   - build a planner prompt (with <replan_context> after the first
        #     attempt)
        #   - run every step and collect artifacts
        #   - if the plan was non-empty AND no artifact survived, this
        #     attempt failed; promote `failure_history` and try again
        #   - stop on success OR when the attempt budget is gone
        failure_history: list[ReplanTrigger] = []
        artifacts: dict[str, dict[str, Any]] = {}
        # Per-run step repetition tracker (MAST FM-1.3). Counts (tool, args)
        # executions across all attempts so the loop can surface looping
        # planners. Reset every `run()` call.
        self._step_repetition = StepRepetitionTracker()
        # Per-run termination guard (MAST FM-1.5, FM-3.1).
        self._termination_guard = TerminationGuard()
        # MVP-14.1 — typed Evidence chain. Built in parallel with
        # `artifacts`; lives at the same scope so the synthesizer (and,
        # later, the Verifier) can consult it.
        chain: ProvenanceChain = ProvenanceChain()
        planner_out: PlannerOutput | None = None
        plan: Plan | None = None
        replan_exhausted = False
        # S2 shadow: set when stagnation is detected, read at the end of the run
        # to report what an early stop would have cost. Never stops anything.
        _stagnation_shadow: dict[str, Any] | None = None
        # S5 shadow: every disagreement seen this run, for the same purpose.
        _disagreement_shadow: list[dict[str, Any]] = []
        # Cheap-path cost gate: set True only when the planner-skip branch
        # below fires for a trivial no-tool turn. Downstream this trims the
        # synthesizer context, forces the LIGHT (cheap) model tier and skips
        # the per-turn knowledge pipeline + memory consolidation — none of
        # which add value for a one-line greeting / config-flag echo.
        cheap_path_active = False
        # MVP-12: advice + forbidden-actions list carried over from the
        # previous attempt's policy.decide() call. Empty on the first
        # attempt; populated on every replan.
        advice_for_planner: str = ""
        forbidden_actions: tuple[tuple[str, str], ...] = ()

        attempt = 0
        while True:
            attempt += 1
            self._current_attempt = attempt

            # Emit a structured `replan_attempt` event for every loop
            # iteration AFTER the first — makes it trivial for tests
            # (and humans) to see how many attempts ran and which advice
            # the planner saw on each.
            if attempt > 1:
                self.log.log(
                    "replan_attempt",
                    {
                        "attempt": attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(advice_for_planner),
                        "forbidden_action_count": len(forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(failure_history)
                        ),
                    },
                )

            failure_context = format_replan_context(
                failure_history,
                attempt,
                self.replan_policy.max_total_replans,
                advice=advice_for_planner,
                forbidden_actions=forbidden_actions,
            )

            if attempt == 1 and forced_sources is not None:
                planner_out = PlannerOutput(
                    reasoning=forced_reasoning,
                    sources=forced_sources,
                    raw_response="",
                    warnings=forced_warnings,
                )
            else:
                # ── Local-critique path (PR2) ─────────────────────────────
                # Resolved referent + critique/show-only → skip planner tools
                # and synthesise from analysis_target (not memory/GK).
                if (
                    local_critique_active
                    and attempt == 1
                    and not failure_context.strip()
                ):
                    planner_out = PlannerOutput(
                        reasoning=(
                            "Local critique path: referent resolved — answering "
                            "from analysis_target without tools or memory/GK."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_local_critique"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "referent_local_critique",
                            "fallback": "local_critique",
                        },
                    )
                    self.log.log(
                        "planner_local_critique",
                        {
                            "question_chars": len(user_question),
                            "kind": (
                                None
                                if self.last_referent_decision is None
                                or self.last_referent_decision.primary is None
                                else self.last_referent_decision.primary.kind
                            ),
                        },
                    )
                # ── Cheap-path gate ───────────────────────────────────────
                # Trivial, no-tool input (config-flag echoes, greetings, one
                # line "what is X") never needs a tool: the planner would only
                # spend a full LLM call to return an empty plan. Skip that call
                # and let the normal empty-plan flow synthesise the answer.
                # Gated to the first attempt with no failure/replan context so
                # replans always get the real planner.
                elif (
                    self.cheap_path_enabled
                    and attempt == 1
                    and not failure_context.strip()
                    and can_skip_planner(user_question, file_hint=file_hint)
                ):
                    planner_out = PlannerOutput(
                        reasoning=(
                            "Cheap path: trivial no-tool input — answering from "
                            "general knowledge and memory without a planner call."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_cheap_path"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "trivial no-tool input",
                            "fallback": "cheap_path",
                        },
                    )
                    self.log.log(
                        "planner_cheap_path",
                        {
                            "question_chars": len(user_question),
                            "reason": "trivial no-tool input",
                        },
                    )
                    cheap_path_active = True
                else:
                    # ── Planner cache ─────────────────────────────────────
                    # Cache key: (question hash, episodic store mtime, file_hint).
                    # Mtime invalidates the cache whenever a new episode is written
                    # (the store changes → the planner might choose different tools).
                    # Only applied on the first attempt with no failure context.
                    _pc_key = (
                        hash(user_question.lower().strip()),
                        self._episodic_store_mtime(),
                        file_hint or "",
                    )
                    if (
                        attempt == 1
                        and not failure_context.strip()
                        and _pc_key in self._planner_cache
                    ):
                        planner_out = self._planner_cache[_pc_key]
                        self.log.log(
                            "planner_cache_hit",
                            {
                                "key_hash": _pc_key[0],
                                "tools_cached": [s["tool"] for s in planner_out.sources],
                            },
                        )
                    else:
                        try:
                            planner_out = self.planner.plan(
                                question=user_question,
                                file_hint=file_hint,
                                history=planner_history,
                                failure_context=failure_context,
                                forbidden_actions=forbidden_actions,
                                llm=_task_planner_llm,
                            )
                        except ModelBudgetExceeded as exc:
                            self._save_budget_pause_checkpoint(
                                _cp,
                                goal=goal,
                                question=user_question,
                                file_hint=file_hint,
                                current_phase="planning",
                                plan=plan,
                                blocked=exc,
                            )
                            raise
                        if (
                            attempt == 1
                            and not failure_context.strip()
                            and "plan_parse_failed" not in planner_out.warnings
                        ):
                            self._planner_cache[_pc_key] = planner_out
                planner_out = force_file_hint_read_when_explicit(
                    planner_out,
                    question=user_question,
                    file_hint=file_hint,
                )
            self.log.log(
                "planner",
                {
                    "reasoning": planner_out.reasoning,
                    "tools_chosen": [s["tool"] for s in planner_out.sources],
                    "warnings": planner_out.warnings,
                    "raw_chars": len(planner_out.raw_response),
                    "attempt": attempt,
                    "replan_context_chars": len(failure_context),
                },
            )
            # Fix #2: surface planner hallucinations as a dedicated event.
            # When the LLM invents a tool name that is not in the registry,
            # _validate_steps drops the step silently.  If every step was
            # dropped (plan_empty_after_drop=True) the loop will proceed to
            # synthesise from general knowledge — making the answer look
            # confident when the actual plan failed.  This event lets
            # operators detect the failure mode without parsing warning strings.
            if planner_out.dropped_tools:
                self.log.log(
                    "plan_tool_drop",
                    {
                        "dropped": planner_out.dropped_tools,
                        "plan_empty_after_drop": not planner_out.sources,
                        "attempt": attempt,
                    },
                )

            # MAST FM-2.6 — reasoning ↔ action consistency check.
            try:
                _ra_report = check_reasoning_actions(
                    planner_out.reasoning,
                    [s["tool"] for s in planner_out.sources],
                )
                if _ra_report.has_mismatch:
                    self.log.log(
                        "reasoning_action_mismatch",
                        {
                            **_ra_report.to_log_payload(),
                            "attempt": attempt,
                        },
                    )
                    # Banked with the episode as well as logged. Still decides
                    # nothing — S4's ruling keeps this an observer — but the
                    # journal is per-run and disappears from the agent's own
                    # memory, which is where a repeated fault has to be visible.
                    self._defect_signals.append("reasoning_action_mismatch")
            except Exception:
                pass  # Observational only — must never abort the loop.

            plan = self._build_plan(goal, planner_out.sources)
            self.log.log("plan", plan, steps=len(plan.steps), attempt=attempt)
            _cp.save_plan(attempt=attempt, step_ids=[s.id for s in plan.steps])

            # Layer 5 — extract plan-level assumptions on the first attempt only.
            if attempt == 1:
                try:
                    _plan_assumptions = extract_from_plan(
                        planner_out.sources,
                        question=user_question,
                        run_id=getattr(self.log, "trace_id", ""),
                    )
                    _run_assumptions.register_many(_plan_assumptions)
                    if _run_assumptions.assumptions:
                        self.log.log(
                            "assumptions_registered",
                            {
                                "count": len(_run_assumptions),
                                "assumptions": _run_assumptions.to_log_payload(),
                            },
                        )
                except Exception:
                    pass  # Never abort the run.

            attempt_artifacts: dict[str, dict[str, Any]] = {}
            attempt_failures: list[ReplanTrigger] = []
            attempt_chain = ProvenanceChain()

            # Planner JSON parse failure: empty `sources` here is NOT an
            # intentional general-knowledge plan, it's a contract break.
            # Without this gate the loop would fall through to the
            # `if not plan.steps or attempt_artifacts: break` branch
            # below and the synthesizer would happily produce a long
            # confident answer from zero evidence. Treat it as a real
            # failure so `replan_policy.decide()` either gets us a clean
            # JSON retry or trips `replan_exhausted` and the synthesizer
            # writes the honest "I could not plan" reply.
            plan_parse_failed = (
                "plan_parse_failed" in (planner_out.warnings or ())
            )
            if plan_parse_failed:
                _parse_diag = dict(getattr(planner_out, "diagnostics", {}) or {})
                self.log.log(
                    "plan_parse_failed",
                    {
                        "attempt": attempt,
                        "warnings": list(planner_out.warnings),
                        "raw_chars": len(planner_out.raw_response),
                        # Sanitised, length-capped preview (no full secrets).
                        "raw_preview": _parse_diag.get("raw_preview")
                        or planner_out.raw_response[:240],
                        "diagnostics": _parse_diag,
                    },
                )
                attempt_failures.append(
                    ReplanTrigger(
                        code="plan_parse_failed",
                        step_id="planner",
                        tool_name=None,
                        arguments={},
                        reason=(
                            "Planner LLM reply did not parse as JSON "
                            f"(raw_chars={len(planner_out.raw_response)})."
                        ),
                        attempt=attempt,
                    )
                )

            for step, outcome, trigger in self._execute_steps_parallel(plan.steps):
                if outcome is None:
                    step.status = "failed"
                    if trigger is not None:
                        attempt_failures.append(trigger)
                    continue
                self._executed_tools.append(outcome["tool"])
                attempt_artifacts[outcome["label"]] = {
                    "tool": outcome["tool"],
                    "output": outcome["output"],
                    "issues": outcome["issues"],
                }
                # MVP-14.1 — typed evidence. The output is already
                # redacted (see _execute_step), so the excerpt that
                # ends up on disk in the chain is safe.
                ev = evidence_from_tool_result(
                    tool_name=outcome["tool"],
                    arguments=outcome.get("arguments"),
                    output=outcome["output"],
                    status="success",
                )
                if ev is not None:
                    attempt_chain.add(ev)
                step.status = "done"
                _cp.save_act(
                    label=outcome["label"],
                    tool=outcome["tool"],
                    chars=len(str(outcome["output"])),
                    status="done",
                )

            # Success: either a 0-step plan (general-knowledge / history-only
            # answer is intentional) or at least one artifact came through.
            # `plan_parse_failed` is NOT success — empty `sources` came from
            # a JSON parse failure, not from the planner choosing zero tools.
            if (not plan.steps and not plan_parse_failed) or attempt_artifacts:
                artifacts = attempt_artifacts
                chain = attempt_chain
                break

            # Failure: this attempt produced nothing usable. Carry the
            # triggers forward and ask the policy what to do next.
            failure_history.extend(attempt_failures)

            # MAST FM-1.5 — stagnation check: same failure signature twice
            # in a row means the loop is looping. Observational only.
            try:
                _stag = self._termination_guard.observe_attempt(
                    attempt=attempt,
                    failure_codes=[t.code for t in attempt_failures],
                    artifact_labels=list(attempt_artifacts.keys()),
                )
                if _stag is not None:
                    self.log.log("stagnation_detected", _stag.to_log_payload())
                    # Shadow accounting (operator ruling 2026-07-27): record
                    # WHERE a stop would have happened, so the run can report at
                    # the end what stopping would have cost or saved. Nothing is
                    # stopped.
                    _stagnation_shadow = {
                        "attempt": attempt,
                        "artifacts_at_detection": sorted(attempt_artifacts.keys()),
                        "repeat_count": _stag.repeat_count,
                        "failure_codes": list(_stag.failure_codes),
                    }
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("stagnation_shadow", exc)

            decision = self.replan_policy.decide(
                failure_history=failure_history,
                completed_attempts=attempt,
            )

            if decision.action == "continue":
                # Log `replan` ONLY when we are going to try again.
                # Pairs neatly with `replan_attempt` (next iteration).
                self.log.log(
                    "replan",
                    {
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": [t.code for t in attempt_failures],
                        "details": [
                            {
                                "step_id": t.step_id,
                                "tool": t.tool_name,
                                "code": t.code,
                                "reason": t.reason,
                            }
                            for t in attempt_failures
                        ],
                        "decision": decision.to_log_payload(),
                    },
                )
                advice_for_planner = decision.advice_for_planner
                forbidden_actions = decision.forbidden_actions
                continue

            # Policy said stop. Emit a structured exhaustion event AND
            # an `error` event (kept for backward-compat with existing
            # consumers that grep on `code=replan_exhausted`). The
            # synthesizer takes over with an honest explanation.
            replan_exhausted = True
            err = ErrorObject(
                source="loop",
                code="replan_exhausted",
                message=(
                    f"Re-planning stopped after {attempt} attempt(s): "
                    f"{decision.reason}. Failure codes: "
                    f"{[t.code for t in failure_history]}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "failure_codes": [t.code for t in failure_history],
                },
            )
            self.log.log("error", err)
            self.log.log(
                "replan_exhausted",
                {
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "triggers": [t.code for t in attempt_failures],
                },
            )
            break

        # planner_out and plan are guaranteed set here (the for loop ran at
        # least once because max_replan_attempts >= 1 is enforced in __init__).
        assert planner_out is not None and plan is not None

        # MVP-14.1 — fold memory & user-directive evidence into the chain.
        # The tool-level evidence was added per step inside the attempt
        # loop; persistent memory and explicit-consent inputs come from
        # different code paths, so we surface them HERE so the Verifier
        # sees a single uniform chain.

        if persistent_block and self.persistent_store is not None:
            # `persistent_block` was built from a small set of records
            # in `_retrieve_persistent`; we replay that retrieval cheaply
            # by re-asking the store for the keyword match.
            #
            # The guard is PER RECORD, not around the loop (MIR-061). With the
            # try outside, one record that failed to convert abandoned the
            # whole loop and every record after it vanished from the chain
            # silently — measured at 1 of 5 arriving — while this comment
            # claimed the loop completed normally. The verifier then judged the
            # answer against a truncated chain, so citations that should have
            # resolved came back `cited_but_unmatched` for a reason unrelated
            # to the answer. The adjacent working-artifact loop below has
            # always had the correct granularity; these two must not drift
            # apart again.
            for rec in self._last_persistent_records:
                try:
                    chain.add(
                        evidence_from_memory_record(
                            record_id=rec.id,
                            content=rec.content,
                            source=getattr(rec, "source", None),
                            created_at=getattr(rec, "created_at", None),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    # Defence-in-depth: chain assembly must NEVER abort the
                    # run. But a dropped record is reported rather than
                    # swallowed — silently, the truncation is indistinguishable
                    # from an ordinary evidence shortfall.
                    self.log.log(
                        "memory_evidence_skipped",
                        {
                            "record_id": getattr(rec, "id", None),
                            "error": type(exc).__name__,
                            "message": str(exc)[:200],
                        },
                    )

        # MVP-14.1b — fold Working Memory (cached tool outputs from prior
        # turns) into the chain so the Verifier can resolve [memory:…]
        # citations that reference conversation-history artefacts.
        # The LLM may generate citation bodies like `turn_3_test_results`;
        # we expose the artefact label and turn index in source_id so the
        # token-overlap fallback has material to work with.
        if self.memory is not None:
            for _art in self.memory.artifacts.values():
                try:
                    _label = str(_art.get("label", ""))
                    _tidx = int(_art.get("turn_index", 0))
                    _output = _art.get("output")
                    if _output is None or not _label:
                        continue
                    # Sanitise label for use in source_id: replace `:` with
                    # `_` so it doesn't confuse the citation prefix parser.
                    _sid_label = _label.replace(":", "_")
                    chain.add(make_evidence(
                        kind="memory",
                        source_id=f"memory:working_turn_{_tidx}_{_sid_label}",
                        obtained_via="working_memory",
                        claim=f"Cached tool output from turn {_tidx}: {_label}",
                        excerpt=str(_output)[:500],
                        confidence=0.85,
                    ))
                except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                    self._sensor_failed("working_memory_evidence", exc)

        # Issue #119 — session-dialogue evidence. Admitted ONLY on a
        # conversational-correction turn: the operator is asking about the
        # exchange, so the exchange is the material. Verbatim, not summarised —
        # the point is that this is a recording. The verifier scopes what it can
        # support (`dialogue_supported`, never `verified`), so admitting it here
        # cannot make an external claim look confirmed.
        if self.last_self_analysis is not None and self.last_self_analysis.is_self_analysis:
            _dialogue_added = 0
            if self.memory is not None:
                for _turn in self.memory.recent_turns(3):
                    try:
                        chain.add(
                            evidence_from_prior_turn(
                                turn_id=_turn.id,
                                turn_index=_turn.index,
                                question=_turn.question,
                                answer=_turn.answer,
                            )
                        )
                        _dialogue_added += 1
                    except Exception as exc:  # noqa: BLE001
                        self.log.log(
                            "dialogue_evidence_skipped",
                            {
                                "turn_id": getattr(_turn, "id", None),
                                "error": type(exc).__name__,
                                "message": str(exc)[:200],
                            },
                        )
            self.log.log(
                "dialogue_evidence_admitted",
                {
                    "turns": _dialogue_added,
                    "reason": self.last_self_analysis.reason,
                },
            )

        # Store the chain on the agent so tests / future Verifier code
        # can consult it after `run()` returns.
        self.last_provenance = chain

        # MAST FM-3.1 — premature completion risk, keyword detector.
        # RETAINED FOR SHADOW COMPARISON ONLY. It is no longer the source of
        # truth: measured at 1/12 recall on phrasings that unambiguously demand
        # a tool, and it fires on «объясни разницу…» because `разниц` is a
        # diff-tool keyword. The obligation check that replaces it runs after
        # composition, and this verdict is carried into its event so the two can
        # be compared on real traffic.
        _premature_keyword_fired = False
        try:
            _pc = self._termination_guard.check_completion(
                question=user_question,
                chain_size=len(chain),
                had_any_artifacts=bool(artifacts),
            )
            if _pc is not None:
                _premature_keyword_fired = True
                self.log.log(
                    "premature_completion_risk", _pc.to_log_payload()
                )
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("premature_completion_risk", exc)

        self.log.log(
            "evidence_collected",
            {
                "count": len(chain),
                "kinds": sorted({ev.kind for ev in chain.evidences}),
                "chain": chain.to_log_payload(),
            },
        )
        source_ranking = rank_chain(chain, question=user_question)
        self.last_source_ranking = source_ranking
        self.log.log("source_ranking", source_ranking.to_log_payload())
        if cheap_path_active:
            # Cheap path: the chain is empty (no tools ran) so the knowledge
            # pipeline and source-registry build have nothing to catalog.
            # Skip them to avoid the per-turn cost the user flagged, and keep
            # the empty registry reset at the top of run().
            source_registry = self.last_source_registry
            self.log.log(
                "knowledge_pipeline_skipped",
                {"reason": "cheap_path", "chain_size": len(chain)},
            )
        else:
            knowledge_result = self.knowledge_pipeline.run(
                chain,
                ranking=source_ranking,
                source_store=self.source_registry_store if may_source_registry else None,
                remember=self._knowledge_remember_batch() if may_knowledge else None,
                auto_write_memory=(
                    self.knowledge_auto_write if may_knowledge else False
                ),
            )
            source_registry = knowledge_result.registry
            self.last_source_registry = source_registry
            self.log.log("source_registry", source_registry.to_log_payload())
            self.last_knowledge_pipeline = knowledge_result
            self.log.log("knowledge_pipeline", knowledge_result.to_log_payload())

        # 7. Respond. When replan exhausted the synthesizer still produces
        # a structured Output Contract reply — it gets the failure history
        # and is told to explain honestly what was tried and why nothing
        # worked. This is a much better UX than a bare error string.
        # Layer 5 — expose current-run assumptions to _synthesize via instance.
        self._run_assumptions_current = _run_assumptions
        # Cheap path: force the LIGHT (cheap/fast) synthesizer tier even when
        # the complexity heuristic would return STANDARD (e.g. a config-flag
        # echo carries no LIGHT signal), and trim the prompt to just the
        # essentials — a one-line greeting/flag never needs long-term memory.
        _synth_llm = _task_synth_llm
        if cheap_path_active:
            try:
                from core.task_complexity import ComplexityTier
                _synth_llm = self.model_router.for_task(
                    ModelRole.SYNTHESIZER,
                    user_question,
                    force_tier=ComplexityTier.LIGHT,
                )
                self.log.log(
                    "cheap_path_synth_model",
                    {"model": getattr(_synth_llm, "model", None)},
                )
            except Exception:  # выбор дешёвой модели не удался — работаем на обычной, это не сбой хода
                _synth_llm = _task_synth_llm
        _saved_on_token = getattr(self, "_stream_on_token", None)

        # Run-local, deliberately NOT an instance attribute. A `self._last_*`
        # field survives the run that set it, and the early-return paths
        # (replay, refusal) bank without ever entering this block — so a
        # declaration from one run would be attributed to the next run's
        # episode. Nothing here outlives the closure.
        _declared: dict[str, str | None] = {"value": None}

        def _do_synthesize(_attempt: SynthAttempt) -> str:
            # Retries must not double-stream tokens: only the first attempt may
            # stream to the console; adapted/retry attempts render silently and
            # the final answer is returned normally.
            if _attempt.index > 0:
                self._stream_on_token = None
            # Cleared BEFORE the call that can raise: an attempt that dies
            # part-way must not leave the previous attempt's verdict standing.
            _declared["value"] = None
            # One nonce per ATTEMPT, not per run: a marker copied out of an
            # attempt that was thrown away must not validate against the one
            # that is actually banked (MIR-057).
            _nonce = new_completion_nonce()
            _raw = self._synthesize(
                completion_nonce=_nonce,
                goal=goal,
                artifacts=artifacts,
                question=user_question,
                planner_reasoning=planner_out.reasoning,
                history=history,
                persistent_block=persistent_block,
                cycle_findings=list(self._cycle_findings),
                failure_history=failure_history if replan_exhausted else None,
                llm=_synth_llm,
                # Shrink the prompt/output on the adapted attempt — this is the
                # recovery for a request the model "could not finish".
                lean_context=cheap_path_active or _attempt.adapt_context,
                local_critique=(
                    self.last_referent_decision
                    if local_critique_active
                    else None
                ),
            )
            # Strip here, once. Everything downstream — the verifier, the
            # user's answer and the stored `full_answer` — is derived from
            # this return value, so one removal keeps all three identical and
            # the nonce reaches none of them.
            _parsed = parse_completion_marker(
                _raw, nonce=_nonce, valid_tokens=_COMPLETION_DECLARATIONS
            )
            _declared["value"] = _parsed.declared
            self.log.log(
                "completion_declaration",
                {
                    # The nonce is a secret of the attempt and is never logged:
                    # a log that carries it would hand forgery back to anyone
                    # who can read logs.
                    "attempt": _attempt.index,
                    "parse": _parsed.status,
                    "declared": _parsed.declared,
                    **({"detail": _parsed.detail} if _parsed.detail else {}),
                },
            )
            return _parsed.text

        try:
            _ladder = run_synthesizer_ladder(
                _do_synthesize,
                build_degraded_answer=build_degraded_synthesis_answer,
                on_event=self.log.log,
                fatal_types=(ModelBudgetExceeded,),
            )
            draft_answer = _ladder.answer
            self._last_synth_degraded = _ladder.degraded
            if _ladder.degraded:
                # The answer the user gets was assembled by the fallback, not
                # by the attempt that declared. Keeping that declaration would
                # attribute a verdict to text its author never wrote.
                _declared["value"] = None
        except ModelBudgetExceeded as exc:
            self._save_budget_pause_checkpoint(
                _cp,
                goal=goal,
                question=user_question,
                file_hint=file_hint,
                current_phase="synthesis",
                plan=plan,
                blocked=exc,
            )
            raise
        finally:
            self._stream_on_token = _saved_on_token

        # 7.5 — MVP-14.4 Verifier. LLM is the DRAFT writer; the Verifier
        # gates what reaches the user. Every claim must be cited (LLM
        # follows the citation grammar in SYSTEM_ANSWER); the Verifier
        # rewrites matched citations to `[verified:<kind>:<src>]` and
        # tags uncited claims with `[unverified]`. A fully-uncited
        # answer earns an explicit disclaimer so the user can never
        # mistake an unsourced answer for a verified one.
        #
        # MVP-14.5 — when the LLM cites [web:URL] but no web_page evidence
        # exists for that URL (Verifier verdict `cited_but_unmatched`),
        # we treat this as a structured failure (`unresolved_citation`)
        # and feed it back through the SAME ReplanPolicy that already
        # governs tool-level failures. The next planner call is told
        # exactly which URLs to fetch; once web_fetch runs, the original
        # draft is re-verified on the enriched chain — no second LLM
        # synthesis is needed because the draft already cites the URLs.
        verifier_failure = False
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
            )
            from core.verifier import (
                verify as _verify,
            )
            from core.verifier_models import VerificationReport as _VRSoft

            try:
                report = _verify(
                    answer=draft_answer,
                    chain=chain,
                    user_question=user_question,
                    expects_contract_headers=getattr(
                        self, "_synthesis_expects_contract_headers", True
                    ),
                    **self._verification_receipt_kwargs(),
                )
            except Exception as _ver_exc:
                # Soft-fail: keep draft; do not pretend "insufficient evidence".
                verifier_failure = True
                self.log.log(
                    "verifier_failure",
                    {
                        "error_type": type(_ver_exc).__name__,
                        "error": str(_ver_exc)[:300],
                        "draft_chars": len(draft_answer),
                        "phase": "initial",
                    },
                )
                report = _VRSoft(
                    total_chunks=0,
                    verified_chunks=0,
                    unverified_chunks=0,
                    cited_but_unmatched_chunks=0,
                    self_declared_chunks=0,
                    structural_chunks=0,
                    chunks=(),
                    annotated_answer=draft_answer,
                    fully_unverified=False,
                    chain_was_empty=True,
                    disclaimer=None,
                    malformed_output=False,
                )
            self.log.log("verification", report.to_log_payload())

            # P1 — observational cross-subsystem audit. Compare planner
            # outcome (steps done/failed, artifacts produced) against
            # verifier verdict and emit a `subsystem_disagreement` event
            # for each conflicting pair. Logging only — no behaviour
            # change at this layer.
            _disagreements: list[dict] = []
            try:
                from core.subsystem_disagreement import detect_disagreements
                _disagreements = detect_disagreements(
                    attempt=attempt,
                    plan_steps=plan.steps,
                    artifacts=artifacts,
                    report=report,
                    failure_history=failure_history,
                )
                for _ev in _disagreements:
                    self.log.log("subsystem_disagreement", _ev)
                    # Shadow accounting (operator ruling 2026-07-27): what a
                    # connected S5 would have done, recorded and never acted on.
                    # Severity decides the action: a full planner/verifier
                    # contradiction is an escalation, the rest is a replan.
                    _disagreement_shadow.append({
                        "kind": _ev.get("kind"),
                        "severity": _ev.get("severity"),
                        "attempt": _ev.get("attempt"),
                        "would_action": (
                            "escalate" if _ev.get("severity") == "high"
                            else "replan"
                        ),
                    })
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("subsystem_disagreement", exc)

            # P1/P2 — confidence vector. Decompose the scalar gate into
            # three axes (evidence / coherence / relevance) so triage
            # can target the right subsystem when something is off.
            # Logging only.
            try:
                from core.confidence_vector import compute_vector
                _cv = compute_vector(
                    report=report,
                    disagreements=_disagreements,
                    question=user_question,
                    answer=draft_answer,
                )
                self.log.log("confidence_vector", _cv.to_log_payload())
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("confidence_vector", exc)
            if report.malformed_output:
                self.log.log(
                    "output_contract_violation",
                    {
                        "reason": "LLM answer contains no Output Contract section headers",
                        "total_chunks": report.total_chunks,
                        "structural_chunks": report.structural_chunks,
                    },
                )
            self.last_verification = report
            self.last_provenance = chain

            # Evidence support — telemetry, never a gate (operator ruling
            # 2026-07-27). Emitted on every verified turn, including the
            # not-applicable ones: "this turn owed no evidence" is exactly the
            # case the old `low_confidence_gate` reported as a zero score, and
            # distinguishing it is the whole point of the rewrite.
            #
            # Applicability is asked with the SAME inputs the enforcing layer
            # uses further down, so observer and enforcer cannot hold opposite
            # opinions about whether evidence was owed on this turn.
            try:
                _ev_expected = is_evidence_expected(
                    role=getattr(self.last_role_context, "role", ""),
                    chain_was_empty=bool(
                        getattr(report, "chain_was_empty", False)
                    ),
                    realtime_required=bool(
                        getattr(self.last_source_ranking, "realtime_required", True)
                    ),
                    answer=draft_answer,
                )
                _support = evaluate_evidence_support(
                    report, evidence_expected=_ev_expected
                )
                self.log.log("evidence_support", _support.to_log_payload())
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("evidence_support", exc)

            verify_replan_attempt = 0
            VERIFY_REPLAN_HARD_CAP = 2  # belt + braces over ReplanPolicy

            while True:
                unresolved_urls = extract_unresolved_web_urls(report)
                if not unresolved_urls:
                    break
                if verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP:
                    self.log.log(
                        "verify_replan_capped",
                        {
                            "attempts": verify_replan_attempt,
                            "hard_cap": VERIFY_REPLAN_HARD_CAP,
                            "unresolved_count": len(unresolved_urls),
                            "unresolved_sample": unresolved_urls[:3],
                        },
                    )
                    break

                verify_replan_attempt += 1
                trigger = ReplanTrigger(
                    code="unresolved_citation",
                    step_id=f"verify-{verify_replan_attempt}",
                    tool_name=None,
                    arguments={"urls": list(unresolved_urls)},
                    reason=(
                        f"Verifier found {len(unresolved_urls)} [web:...] "
                        f"citation(s) the chain cannot resolve. "
                        f"Planner must add web_fetch for: {unresolved_urls}"
                    ),
                    attempt=attempt + verify_replan_attempt,
                )
                failure_history.append(trigger)

                decision = self.replan_policy.decide(
                    failure_history=failure_history,
                    completed_attempts=attempt + verify_replan_attempt,
                )

                if decision.action != "continue":
                    replan_exhausted = True
                    self.log.log(
                        "replan_exhausted",
                        {
                            "phase": "verify",
                            "attempts": attempt + verify_replan_attempt,
                            "max_total": self.replan_policy.max_total_replans,
                            "decision_action": decision.action,
                            "decision_reason": decision.reason,
                            "failure_counts": dict(decision.failure_counts),
                            "triggers": ["unresolved_citation"],
                        },
                    )
                    break

                # Build the planner advice. We surface `unresolved_citation`
                # advice FIRST (the new failure dominates) and then append
                # an explicit URL list the planner must convert into
                # web_fetch steps. The base `decision.advice_for_planner`
                # is composed over all FailureTypes seen so far, in
                # FailureType-declaration order, so it might bury the
                # critical fetch instruction — we prepend ours explicitly.
                base_advice = (
                    self.replan_policy.budgets["unresolved_citation"].advice
                )
                urls_block = "\n".join(f"  - {u}" for u in unresolved_urls)
                verify_advice = (
                    f"{base_advice}\n\n"
                    f"URLs that MUST be opened via web_fetch (one step each):"
                    f"\n{urls_block}"
                )

                self.log.log(
                    "replan",
                    {
                        "phase": "verify",
                        "attempt": attempt + verify_replan_attempt - 1,
                        "next_attempt": attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": ["unresolved_citation"],
                        "details": [
                            {
                                "step_id": trigger.step_id,
                                "tool": None,
                                "code": "unresolved_citation",
                                "reason": trigger.reason,
                            },
                        ],
                        "decision": decision.to_log_payload(),
                        "unresolved_urls": list(unresolved_urls),
                    },
                )

                # Bump the public attempt counter so `respond.attempts_used`
                # reflects the verify-driven re-plans honestly.
                self._current_attempt = attempt + verify_replan_attempt

                self.log.log(
                    "replan_attempt",
                    {
                        "phase": "verify",
                        "attempt": attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(verify_advice),
                        "forbidden_action_count": len(decision.forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(failure_history)
                        ),
                    },
                )

                failure_context = format_replan_context(
                    failure_history,
                    attempt + verify_replan_attempt,
                    self.replan_policy.max_total_replans,
                    advice=verify_advice,
                    forbidden_actions=decision.forbidden_actions,
                )

                try:
                    planner_out = self.planner.plan(
                        question=user_question,
                        file_hint=file_hint,
                        history=planner_history,
                        failure_context=failure_context,
                        forbidden_actions=decision.forbidden_actions,
                        # Keep the complexity-escalated planner model on verify
                        # re-plans; without this the re-plan silently dropped to
                        # the default tier a "deep" question was escalated away
                        # from.
                        llm=_task_planner_llm,
                    )
                except ModelBudgetExceeded as exc:
                    self._save_budget_pause_checkpoint(
                        _cp,
                        goal=goal,
                        question=user_question,
                        file_hint=file_hint,
                        current_phase="verification_replan",
                        plan=plan,
                        blocked=exc,
                    )
                    raise
                planner_out = force_file_hint_read_when_explicit(
                    planner_out,
                    question=user_question,
                    file_hint=file_hint,
                )
                self.log.log(
                    "planner",
                    {
                        "phase": "verify",
                        "reasoning": planner_out.reasoning,
                        "tools_chosen": [s["tool"] for s in planner_out.sources],
                        "warnings": planner_out.warnings,
                        "raw_chars": len(planner_out.raw_response),
                        "attempt": attempt + verify_replan_attempt,
                        "replan_context_chars": len(failure_context),
                    },
                )

                verify_plan = self._build_plan(goal, planner_out.sources)
                self.log.log(
                    "plan",
                    verify_plan,
                    steps=len(verify_plan.steps),
                    attempt=attempt + verify_replan_attempt,
                    phase="verify",
                )

                # Execute the new steps. We tolerate sanitiser/policy drops
                # and per-step failures — if NOTHING gets fetched we'll
                # just exit on the next loop iteration when re-verify
                # still finds the same unresolved URLs. No infinite loop
                # because the hard cap + per-type budget both bound us.
                added_evidence = 0
                for step, outcome, trigger in self._execute_steps_parallel(verify_plan.steps):
                    if outcome is None:
                        step.status = "failed"
                        # Drain the scratch trigger so it doesn't leak into
                        # the next decide() iteration with a misleading code
                        # (e.g. tool_error for a fetch that was sanitised).
                        if trigger is not None:
                            failure_history.append(trigger)
                        continue
                    self._executed_tools.append(outcome["tool"])
                    artifacts[outcome["label"]] = {
                        "tool": outcome["tool"],
                        "output": outcome["output"],
                        "issues": outcome["issues"],
                    }
                    ev = evidence_from_tool_result(
                        tool_name=outcome["tool"],
                        arguments=outcome.get("arguments"),
                        output=outcome["output"],
                        status="success",
                    )
                    if ev is not None:
                        chain.add(ev)
                        added_evidence += 1
                    step.status = "done"

                # Re-verify the ORIGINAL draft against the enriched chain.
                # The draft already cites these URLs (that's why they
                # were unresolved); now the chain has the web_page
                # evidence so `match_citation` will resolve them.
                try:
                    report = _verify(
                        answer=draft_answer,
                        chain=chain,
                        user_question=user_question,
                        expects_contract_headers=getattr(
                            self, "_synthesis_expects_contract_headers", True
                        ),
                        **self._verification_receipt_kwargs(),
                    )
                except Exception as _ver_exc:
                    verifier_failure = True
                    self.log.log(
                        "verifier_failure",
                        {
                            "error_type": type(_ver_exc).__name__,
                            "error": str(_ver_exc)[:300],
                            "draft_chars": len(draft_answer),
                            "phase": "verify_replan",
                            "iteration": verify_replan_attempt,
                        },
                    )
                    break
                self.log.log(
                    "verification",
                    {
                        **report.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                        "evidence_added": added_evidence,
                    },
                )
                self.last_verification = report
                self.last_provenance = chain

                # Re-emit the chain snapshot so callers / log consumers
                # see the enriched provenance after each fetch round.
                self.log.log(
                    "evidence_collected",
                    {
                        "phase": "verify",
                        "count": len(chain),
                        "kinds": sorted({ev.kind for ev in chain.evidences}),
                        "chain": chain.to_log_payload(),
                    },
                )
                source_ranking = rank_chain(chain, question=user_question)
                self.last_source_ranking = source_ranking
                self.log.log(
                    "source_ranking",
                    {
                        **source_ranking.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                knowledge_result = self.knowledge_pipeline.run(
                    chain,
                    ranking=source_ranking,
                    source_store=(
                        self.source_registry_store if may_source_registry else None
                    ),
                    remember=(
                        self._knowledge_remember_batch()
                        if may_knowledge
                        else None
                    ),
                    auto_write_memory=(
                        self.knowledge_auto_write if may_knowledge else False
                    ),
                )
                source_registry = knowledge_result.registry
                self._quarantine_conflicted_memory(knowledge_result)
                self.last_source_registry = source_registry
                self.log.log(
                    "source_registry",
                    {
                        **source_registry.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                self.last_knowledge_pipeline = knowledge_result
                self.log.log(
                    "knowledge_pipeline",
                    {
                        **knowledge_result.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )

                # If the fetch round added zero evidence, re-verify will
                # produce the same unresolved list — exit instead of
                # looping back to a planner call that has nothing new
                # to work with.
                if added_evidence == 0:
                    self.log.log(
                        "verify_replan_noop",
                        {
                            "iteration": verify_replan_attempt,
                            "unresolved_count_before": len(unresolved_urls),
                            "unresolved_count_after": len(
                                extract_unresolved_web_urls(report)
                            ),
                        },
                    )
                    break

            answer = report.annotated_answer
        else:
            answer = draft_answer
            self.last_verification = None

        # From here the response is a DRAFT, not a string. Deciders below either
        # rewrite the claims (`set_body`) or attach something about them
        # (`add_notice`); composition happens once, at `render()`. Before this,
        # everything wrote to one variable and the last writer won — which is how
        # a truncation could delete the clarifying questions the loop had just
        # decided to ask (measured; see core/response_draft.py).
        draft = ResponseDraft(body=answer)

        # MIR-069 (phase 1): the five-point verification explanation — what was
        # checked, how, on what evidence, what remains unverified, how
        # confident. Full text goes to the journal; the compact tail rides the
        # notice ledger so a later body rewrite cannot delete it. Nothing
        # examined → no tail (the disclaimers already speak for that case).
        if self.last_verification is not None:
            try:
                _vsummary = build_verification_summary(
                    self.last_verification, chain=self.last_provenance
                )
                self.log.log(
                    "verification_explained", _vsummary.to_log_payload()
                )
                if _vsummary.tail:
                    draft.add_notice(
                        author="verification_summary",
                        channel="append",
                        text=_vsummary.tail,
                    )
            except Exception as _vs_exc:
                # The explanation must never break the answer — but its
                # failure must not be invisible either (review round #283):
                # the journal says why this turn carries no explanation.
                try:
                    self.log.log(
                        "verification_explained_failed",
                        {
                            "error_type": type(_vs_exc).__name__,
                            "error": str(_vs_exc)[:300],
                        },
                    )
                except Exception:
                    pass

        # MIR-074 phase 1 (operator ruling): STRONG causal credit. A record
        # cited [memory:<id>] in a chunk the verifier marked `verified` has
        # completed the full chain — retrieved → changed the answer →
        # independently checked. Injection alone stays a near-zero signal
        # (access_count); this is the one that counts.
        if (
            self.last_verification is not None
            and self.last_provenance is not None
            and self.persistent_store is not None
            and not self._durable_learning_suppressed("access_stats")
        ):
            try:
                _ev_by_id = {
                    ev.id: ev for ev in self.last_provenance.evidences
                }
                _credited: list[str] = []
                _seen_rids: set[str] = set()
                for _chunk in self.last_verification.chunks:
                    if _chunk.verdict != "verified":
                        continue
                    for _mid in _chunk.matched_evidence_ids:
                        _ev = _ev_by_id.get(_mid)
                        if (
                            _ev is not None
                            and _ev.obtained_via == "memory"
                            and _ev.source_id.startswith("memory:mem")
                        ):
                            _rid = _ev.source_id.removeprefix("memory:")
                            if _rid not in _seen_rids:
                                _seen_rids.add(_rid)
                                _credited.append(_rid)
                if _credited:
                    # One load, all increments in memory, ONE rewrite — an
                    # answer crediting N records must not trigger N full-file
                    # rewrites (review round #294).
                    _records = self.persistent_store.load()
                    _updated: list[str] = []
                    _new_records = []
                    for _rec in _records:
                        if _rec.id in _seen_rids:
                            _new_records.append(
                                _rec.model_copy(
                                    update={"causal_use": _rec.causal_use + 1}
                                )
                            )
                            _updated.append(_rec.id)
                        else:
                            _new_records.append(_rec)
                    if _updated:
                        self.persistent_store._rewrite(_new_records)
                        self.log.log(
                            "memory_causal_credit",
                            {"record_ids": _updated, "count": len(_updated)},
                        )
            except Exception as _cc_exc:
                # Credit must never break the answer — and its failure must
                # not be invisible (the MIR-077 rule).
                try:
                    self.log.log(
                        "memory_causal_credit_failed",
                        {
                            "error_type": type(_cc_exc).__name__,
                            "error": str(_cc_exc)[:300],
                        },
                    )
                except Exception:
                    pass

        # MIR-075: ask back instead of only philosophising unsupported. Fires
        # ONLY when the self-analysis sensor marked this turn AND the answer's
        # own verification counted zero verified chunks over a non-empty claim
        # set — the operator's measured «он не переспрашивает» shape. Question
        # wording is never inspected (the lexical route died in #263).
        if (
            self.last_verification is not None
            and self.last_verification.total_chunks > 0
            and self.last_verification.verified_chunks == 0
            and getattr(self.last_self_analysis, "is_self_analysis", False)
        ):
            try:
                from core.clarification_gate import build_self_analysis_ask_back
                _ask = build_self_analysis_ask_back()
                if draft.add_notice(
                    author="clarification_gate",
                    channel="append",
                    text=_ask,
                ):
                    self.log.log(
                        "clarification_ask_back",
                        {
                            "reason": "self_analysis_zero_verified",
                            "total_chunks": self.last_verification.total_chunks,
                            "self_declared_chunks": (
                                self.last_verification.self_declared_chunks
                            ),
                        },
                    )
            except Exception as _ab_exc:
                try:
                    self.log.log(
                        "clarification_ask_back_failed",
                        {
                            "error_type": type(_ab_exc).__name__,
                            "error": str(_ab_exc)[:300],
                        },
                    )
                except Exception:
                    pass

        policy_result = apply_ranker_output_policy(
            answer=draft.body,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            # Body edits (capped Confidence, downgraded realtime tags) are
            # corrections to the claims; the warnings are about the run and are
            # composed onto whatever body survives.
            draft.set_body(policy_result.answer, by="output_policy")
            for _warning in policy_result.warnings:
                draft.add_notice(
                    author="output_policy",
                    channel="unverified_note",
                    text=_warning,
                )
            self.log.log("output_policy", policy_result.to_log_payload())

        # B-1 Clarification Gate — режим переспроса. When the loop is STUCK
        # (replan exhausted == loop_suspected), the mature response is to ASK,
        # not to keep building. The gate's minimal clarifying questions go above
        # the honest answer so the operator can narrow the frame. Pure and
        # deterministic (no LLM, no I/O); best-effort so it can never take down
        # the response path.
        if replan_exhausted and self.clarification_gate_enabled:
            try:
                from core.clarification_gate import clarification_for_replan_exhausted
                _clarify = clarification_for_replan_exhausted()
                if draft.add_notice(
                    author="clarification_gate",
                    channel="prepend",
                    text=_clarify.prompt(),
                ):
                    self.log.log("clarification_gate", _clarify.to_dict())
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("clarification_gate", exc)

        # Answer enforcement (PR3): low-evidence truncation, local-critique
        # empty-rewrite skip, verifier soft-fail, claim-level short path.
        # Evidence support stays observational; this is the structural layer.
        try:
            _ranking = self.last_source_ranking
            _report = self.last_verification
            _chain_empty = bool(
                getattr(_report, "chain_was_empty", False)
            ) if _report is not None else True
            _realtime = (
                bool(getattr(_ranking, "realtime_required", True))
                if _ranking is not None
                else True
            )
            _evidence_expected = is_evidence_expected(
                role=getattr(self.last_role_context, "role", ""),
                chain_was_empty=_chain_empty,
                realtime_required=_realtime,
                answer=draft.body,
            )
            # Enforcement judges the CLAIMS, so it is handed the body alone.
            # Handing it the composed text would let it measure — and delete —
            # notices that are not claims and that no verdict about the evidence
            # can make untrue.
            _enf = apply_answer_enforcement(
                answer=draft.body,
                report=_report,
                question=user_question,
                evidence_expected=_evidence_expected,
                local_critique_active=local_critique_active,
                verifier_failure=verifier_failure,
            )
            self.log.log("answer_enforcement", _enf.to_log_payload())
            if _enf.outcome == "insufficient_evidence" and _enf.applied:
                self.log.log(
                    "low_evidence_truncation",
                    _enf.low_evidence_payload or _enf.to_log_payload(),
                )
            if _enf.applied:
                draft.set_body(_enf.answer, by="answer_enforcement")
        except Exception:
            # Defence-in-depth: truncation must NEVER take down
            # the loop. A failed evaluation falls back to the
            # original answer the user would otherwise have got.
            pass

        scope_notice = file_scope_notice(user_question, artifacts)
        if draft.add_notice(
            author="file_scope",
            channel="prepend",
            text=scope_notice,
        ):
            self.log.log(
                "file_scope_notice",
                {
                    "notice": scope_notice,
                    "artifact_labels": list(artifacts.keys()),
                },
            )

        # ── Compose ─────────────────────────────────────────────────────────
        # The single arbitration point: claims and notices are joined here and
        # nowhere else, so no decider can silently outrank another by running
        # later. The journal carries the ledger, including anything that failed
        # to survive — a contribution that goes missing is now visible instead
        # of having to be found by reading the code.
        answer = draft.render()
        self.log.log("response_composed", draft.to_log_payload(answer))

        # Strip internal verification markers before user-facing output.
        # Must happen AFTER output_policy which needs [verified:...] markers.
        answer = _strip_verification_markers(answer)

        # Premature completion, asked as an OBLIGATION question rather than a
        # keyword question (S3). Runs here, after composition, because three of
        # the four obligation states turn on whether the operator was actually
        # told — and that can only be read off the answer they receive.
        # Observational; the old keyword detector still fires above, so the two
        # can be compared in the journal before anything is decided.
        try:
            _denied = tuple(
                str(getattr(t, "tool_name", "") or "")
                for t in failure_history
                if getattr(t, "code", "") == "policy_blocked"
            )
            _obl = evaluate_completion_obligations(
                question=user_question,
                answer=answer,
                plan_steps=list(getattr(plan, "steps", ()) or ()),
                artifacts=artifacts,
                chain_size=len(chain),
                realtime_required=bool(
                    getattr(self.last_source_ranking, "realtime_required", False)
                ),
                file_hint=file_hint,
                failure_codes=[
                    str(getattr(t, "code", "") or "") for t in failure_history
                ],
                denied_tools=_denied,
                contract=completion_contract,
            )
            _payload = _obl.to_log_payload()
            # Shadow comparison against the detector this replaces, so the
            # disagreement between them is a number in the journal rather than
            # something a later reader has to reconstruct.
            _payload["shadow_keyword_detector"] = bool(_premature_keyword_fired)
            self.log.log("completion_obligation", _payload)
            # Recorded here, enforced at banking — not mid-run. This signal IS
            # authoritative: `assemble_completion_verdict` lowers a claim of
            # `achieved` to `partially_achieved` when it is present, which also
            # withholds procedure credit. Nothing is stopped or replanned while
            # the cycle is still running, so the run's own path is unchanged;
            # what changes is the verdict it is banked under. S3's ruling was
            # "keep the requirement, replace the detector", and the requirement
            # is what carries that authority.
            if _obl.triggered:
                self._defect_signals.append("obligation_silently_missing")
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("completion_obligation", exc)

        # Defence-in-depth: redact once more on the way out so even an
        # LLM hallucinating a credential or PII cannot bypass the kernel.
        safe_answer, answer_findings, answer_pii_findings = redact_dlp_text(answer)
        if answer_findings:
            self.log.log(
                "secret_detected",
                {
                    "label": "final_answer",
                    "kinds": sorted({f.kind for f in answer_findings}),
                    "count": len(answer_findings),
                    "surface": "user_output",
                },
            )
        if answer_pii_findings:
            pii_kinds = sorted({f"pii-{f.kind}" for f in answer_pii_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "final_answer",
                    "kinds": pii_kinds,
                    "count": len(answer_pii_findings),
                    "surface": "user_output",
                },
            )
        answer = safe_answer

        # ── Sensor shadow accounting (S2, S5) ───────────────────────────────
        # Emitted at the end of the run because the interesting question —
        # "would stopping there have changed anything?" — can only be answered
        # once it is known what the remaining attempts actually produced.
        # Reported, never acted on: neither sensor stops or replans anything.
        if _stagnation_shadow is not None:
            try:
                _at = int(_stagnation_shadow.get("attempt") or 0)
                _seen_then = set(_stagnation_shadow.get("artifacts_at_detection") or ())
                _new_after = sorted(set(artifacts) - _seen_then)
                self.log.log("stagnation_shadow", {
                    **_stagnation_shadow,
                    "would_stop": True,
                    "would_save_attempts": max(0, self._current_attempt - _at),
                    # The honest form of "would it have changed the result":
                    # did anything new actually arrive after the stop point?
                    "would_change_result": bool(_new_after),
                    "artifacts_gained_after_detection": _new_after,
                    "replan_exhausted": replan_exhausted,
                })
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("stagnation_shadow_outcome", exc)
        if _disagreement_shadow:
            try:
                self.log.log("subsystem_disagreement_shadow", {
                    "events": _disagreement_shadow,
                    "would_escalate": sum(
                        1 for d in _disagreement_shadow
                        if d.get("would_action") == "escalate"
                    ),
                    "would_replan": sum(
                        1 for d in _disagreement_shadow
                        if d.get("would_action") == "replan"
                    ),
                    "attempts_used": self._current_attempt,
                    "replan_exhausted": replan_exhausted,
                })
            except Exception:
                pass

        self.log.log(
            "respond",
            {
                "chars": len(answer),
                "sources": list(artifacts.keys()) or ["general-knowledge"],
                "redactions": len(self._cycle_findings or []),
                "attempts_used": self._current_attempt,
                "replan_exhausted": replan_exhausted,
            },
        )
        _cp.save_respond(answer=answer)

        # Memory write — append the completed turn
        if self.memory is not None:
            turn = self.memory.record_turn(
                question=user_question,
                planner_reasoning=planner_out.reasoning,
                tools_used=[s["tool"] for s in planner_out.sources],
                artifact_labels=list(artifacts.keys()),
                answer=answer,
            )
            self.log.log(
                "memory_write",
                {
                    "session_id": self.memory.session_id,
                    "turn_id": turn.id,
                    "turn_index": turn.index,
                    "tools_used": turn.tools_used,
                    "labels": turn.artifact_labels,
                    },
                )
            # Anthropic 2025 context engineering — compact older turns into
            # one summary Turn instead of silently dropping them when
            # max_turns is exceeded. No-op until the threshold is crossed.
            try:
                if self.memory.compact_if_needed():
                    self.log.log(
                        "memory_compacted",
                        {
                            "session_id": self.memory.session_id,
                            "turns_after": len(self.memory.turns),
                        },
                    )
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("memory_compaction", exc)

        verification = self.last_verification
        weak_chunks = 0
        if verification:
            weak_chunks = (
                verification.subagent_asserted_chunks
                + verification.cited_but_unmatched_chunks
                + verification.receipt_missing_chunks
                + verification.topic_supported_but_claim_unverified_chunks
                # Issue #119: a self-analysis answer is legitimately shippable
                # and legitimately NOT a reusable procedure. Counted as weak so
                # `episode_from_agent_cycle` banks it `partial`, not `success` —
                # otherwise verified=0/unverified=0 would score a perfect run
                # (MIR-002) and promote "explaining my own mistake" into
                # procedural memory.
                + verification.dialogue_supported_chunks
                # Operator ruling 2026-08-03 (MIR-028): support that is only
                # the operator's own words must not bank a clean success —
                # counted weak, so the episode lands `partial` when user-echo
                # is all (or most of) what the answer leans on.
                + verification.user_asserted_chunks
            )
        # Layer 4 — update user profile from this interaction.
        if may_profile and self.user_profile_store is not None:
            try:
                updated = self.user_profile_store.update_from_interaction(
                    question=user_question,
                    response=answer,
                    base=self.last_user_profile,
                )
                self.last_user_profile = updated
                self.log.log(
                    "user_profile_update",
                    {
                        "expertise": updated.expertise,
                        "verbosity": updated.verbosity,
                        "language": updated.language,
                        "interaction_count": updated.interaction_count,
                        "interests": updated.interests,
                        "expert_signals": updated.expert_signals,
                        "novice_signals": updated.novice_signals,
                    },
                )
            except Exception:
                pass  # Profile update must never abort the run.

        # Layer 5 — persist assumptions and expose via last_assumptions.
        self.last_assumptions = _run_assumptions
        if (
            may_assumptions
            and self.assumption_store is not None
            and _run_assumptions.new_assumptions
        ):
            try:
                self.assumption_store.save_many(_run_assumptions.new_assumptions)
            except Exception:
                pass  # Store failure must never abort the run.

        # ── Bank the episode LAST ────────────────────────────────────────────
        # A `success` outcome may only be recorded once the run has actually
        # finished. Writing it earlier leaves a window where a later failure
        # would abort the run with a success already banked — and idempotency
        # (keyed on run_id) would then refuse to correct it.
        #
        # No outer permission gate here: episode, procedure and consolidation
        # are three separate sinks, and `_record_experience_memory` resolves
        # each one. Gating the whole call would make "bank an episode but
        # promote no procedure" unreachable.
        self._record_experience_memory(
            goal_description=goal.description,
            question=user_question,
            answer=answer,
            tools_used=[s["tool"] for s in planner_out.sources],
            source_labels=list(artifacts.keys()) or ["general-knowledge"],
            verified_chunks=verification.verified_chunks if verification else 0,
            unverified_chunks=verification.unverified_chunks if verification else 0,
            weak_chunks=weak_chunks,
            replan_exhausted=replan_exhausted,
            skip_consolidation=cheap_path_active,
            # Set by either soft-fail site (`:1625` initial, `:1929` replan).
            # Both write this same local, which is why one flag covers them.
            verifier_failure=verifier_failure,
            # Run-local: the verdict of the synthesis attempt that produced
            # THIS answer, or None when the ladder degraded.
            declared_completion=_declared["value"],
        )

        # Clear streaming callback so it cannot leak into the next turn.
        self._stream_on_token = None
        self.last_replan_exhausted = bool(replan_exhausted)
        return answer

    # ---------- persistent memory facade ----------


    # ------------------------------------------------------------------
    # MVP-11 Compensation surface (rollback / undo)
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # MVP-10 Memory Hygiene surface
    # ------------------------------------------------------------------
    # Every hygiene operation is a discrete, deliberate call. The CLI
    # exposes them via `:hygiene <subcmd>`. Each method logs ONE event
    # carrying the typed report's `summary()` so audits show exactly
    # what was removed and why.


    # ---------- replan helpers ----------

    # ---------- phase implementations ----------


    def _check_clarification(self, user_question: str) -> ClarificationResult:
        """Run the Clarification Policy (§3) — pure heuristic, no LLM."""
        from core.clarification_policy import check_clarification
        return check_clarification(user_question)

    def _check_operational_domain(self, user_question: str) -> DomainResult:
        """Run the Operational Design Domain gate (§7 ODD) — pure, no LLM."""
        from core.operational_domain import check_operational_domain
        return check_operational_domain(user_question)

    def _maybe_resolve_referent(
        self,
        user_question: str,
        *,
        file_hint: str | None,
    ) -> None:
        """Shadow/on referent resolution. Shadow logs only; ``on`` enables PR2 path."""
        mode = referent_resolver_mode()
        if mode == "off":
            self.last_referent_decision = None
            return
        try:
            run_id = str(getattr(self.log, "trace_id", "") or new_id("run"))
            session_id = (
                self.memory.session_id if self.memory is not None else run_id
            )
            prior_turns: list[PriorTurnRef] = []
            artifacts = []
            if self.memory is not None:
                prior_turns = [
                    PriorTurnRef(
                        turn_id=turn.id,
                        session_id=session_id,
                        question=turn.question,
                        answer=turn.answer,
                        timestamp=turn.timestamp,
                    )
                    for turn in self.memory.recent_turns(5)
                ]
                artifacts = artifacts_from_working_memory(
                    self.memory.artifacts,
                    session_id=session_id,
                )
            hint_ref: FileHintRef | None = None
            if file_hint and str(file_hint).strip():
                hint_ref = FileHintRef(
                    path=str(file_hint).strip(),
                    turn_id=run_id,
                    session_id=session_id,
                )
            resolver = ReferentResolver(
                workspace_root=self._file_read_workspace_root(),
            )
            decision = resolver.resolve(
                user_question,
                current_session_id=session_id,
                current_turn_id=run_id,
                file_hint=hint_ref,
                artifacts=artifacts,
                prior_turns=tuple(prior_turns),
            )
            self.last_referent_decision = decision
            eligible = is_local_critique_eligible(decision)
            payload = decision.to_dict()
            payload["mode"] = mode
            payload["local_critique_eligible"] = eligible
            # True when enabling ``on`` would change the answer path (PR2).
            payload["would_change_answer"] = eligible
            self.log.log("referent_decision", payload)
        except Exception:
            # Observability must never abort the run.
            self.last_referent_decision = None

    def _build_plan(self, goal: Goal, sources: list[dict[str, Any]]) -> Plan:
        plan = Plan(goal_id=goal.id)
        for i, src in enumerate(sources, start=1):
            plan.steps.append(  # pylint: disable=no-member  # pydantic list field, real list at runtime
                PlanStep(
                    plan_id=plan.id,
                    order=i,
                    action_spec={
                        "type": "tool_call",
                        "tool_name": src["tool"],
                        "arguments": src["arguments"],
                        "source_label": src["label"],
                    },
                    expected_outcome=src["expected_outcome"],
                )
            )
        plan.status = "in_progress"
        return plan

    @staticmethod
    def _checkpoint_step_summaries(plan: Plan | None) -> list[dict[str, Any]]:
        if plan is None:
            return []
        summaries: list[dict[str, Any]] = []
        for step in plan.steps:
            action = step.action_spec or {}
            summaries.append(
                {
                    "id": step.id,
                    "order": step.order,
                    "tool": action.get("tool_name") or action.get("tool"),
                    "source_label": action.get("source_label"),
                    "status": step.status,
                    "expected_outcome": step.expected_outcome,
                }
            )
        return summaries

    def _save_budget_pause_checkpoint(
        self,
        checkpoint: Any,
        *,
        goal: Goal,
        question: str,
        file_hint: str | None,
        current_phase: str,
        plan: Plan | None,
        blocked: ModelBudgetExceeded,
    ) -> None:
        save_paused = getattr(checkpoint, "save_paused", None)
        if not callable(save_paused):
            return
        planned_steps = self._checkpoint_step_summaries(plan)
        completed_steps = [
            step for step in planned_steps if step.get("status") == "done"
        ]
        remaining_steps = [
            step for step in planned_steps if step.get("status") != "done"
        ]
        payload = {
            "active_goal": goal.description,
            "goal_id": goal.id,
            "original_user_question": question,
            "file_hint": file_hint,
            "current_phase": current_phase,
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "remaining_steps": remaining_steps,
            "stop_reason": "budget_exhausted",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "blocked_model": blocked.to_dict(),
        }
        try:
            save_paused(payload)
            self.log.log(
                "resumable_checkpoint_paused",
                {
                    "current_phase": current_phase,
                    "stop_reason": "budget_exhausted",
                    "planned_steps": len(planned_steps),
                    "completed_steps": len(completed_steps),
                    "remaining_steps": len(remaining_steps),
                    "blocked_model": payload["blocked_model"],
                },
            )
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("budget_exhaustion_log", exc)

    def _file_read_workspace_root(self) -> Path | None:
        try:
            tool = self.registry.get("file_read")
        except KeyError:
            return None
        root = getattr(tool, "workspace_root", None)
        if root is None:
            return None
        return Path(root).resolve()

    def _verification_receipt_kwargs(self) -> dict[str, Any]:
        from core.tool_receipts import ToolReceiptLedger, default_receipts_path

        log = getattr(self, "log", None)
        trace_id = str(getattr(log, "trace_id", "") or "") if log is not None else ""
        root = self._file_read_workspace_root()
        if root is None:
            return {"receipt_ledger": None, "trace_id": trace_id or None}
        return {
            "receipt_ledger": ToolReceiptLedger(default_receipts_path(root)),
            "trace_id": trace_id or None,
        }








    # ---------- response synthesis ----------

    def _synthesize(
        self,
        goal: Goal,
        artifacts: dict[str, dict[str, Any]],
        question: str,
        planner_reasoning: str,
        history: str = "",
        persistent_block: str = "",
        cycle_findings: list[dict[str, Any]] | None = None,
        failure_history: list[ReplanTrigger] | None = None,
        llm=None,
        lean_context: bool = False,
        local_critique: ReferentDecision | None = None,
        completion_nonce: str = "",
    ) -> str:
        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        # Cheap path: drop the heavy, question-irrelevant injections
        # (long-term memory, user profile, run assumptions). A trivial
        # greeting / config-flag echo is answered from general knowledge;
        # these blocks only inflate the prompt token count.
        # Local critique: keep profile (language/verbosity) but drop LTM,
        # assumptions, role, and conversation_history — target is explicit.
        if lean_context or local_critique is not None:
            long_term_block = ""
            assumptions_block = ""
            role_block = ""
            if local_critique is not None:
                history_block = ""
                profile_block = (
                    profile_to_prompt_block(self.last_user_profile) + "\n\n"
                    if self.last_user_profile is not None
                    else ""
                )
            else:
                profile_block = ""
        else:
            long_term_block = (
                f"{persistent_block}\n\n" if persistent_block.strip() else ""
            )
            role_block = self.last_role_context.to_prompt_block() + "\n\n"
            profile_block = (
                profile_to_prompt_block(self.last_user_profile) + "\n\n"
                if self.last_user_profile is not None
                else ""
            )
            # Layer 5 — inject active assumptions into synthesizer.
            _assumptions_src = getattr(self, "_run_assumptions_current", None)
            assumptions_block = (
                _assumptions_src.to_prompt_block() + "\n\n"
                if _assumptions_src is not None and len(_assumptions_src) > 0
                else ""
            )

        # Kernel-built safety notes — the LLM is told to surface these in
        # the user-facing answer. The notes describe what was redacted
        # (the kernel did it), not what the LLM did.
        safety_block = ""
        if cycle_findings:
            lines = ["<safety_notes>"]
            lines.extend(
                f"- label={f['label']} kinds={f['kinds']} "
                f"count={f['count']} (kernel-redacted)"
                for f in cycle_findings
            )
            lines.append("</safety_notes>")
            safety_block = "\n".join(lines) + "\n\n"

        # Failure context (MVP-8). Only injected when re-planning was
        # exhausted; carries the cumulative trigger list so the
        # synthesizer can write an honest Conclusion ("I tried X, Y, Z;
        # here is why none worked") instead of an empty/fake answer.
        failure_block = ""
        if failure_history:
            lines = ["<failure_context>"]
            lines.append(
                "Re-planning was exhausted after every attempt failed."
            )
            for trig in failure_history:
                lines.append(
                    f"- attempt={trig.attempt} code={trig.code} "
                    f"tool={trig.tool_name or '(none)'}: {trig.reason}"
                )
            lines.append("</failure_context>")
            failure_block = "\n".join(lines) + "\n\n"

        # The question travels into the LLM prompt; redact any credential
        # or sensitive PII the user pasted in.
        safe_question, _q_findings, _q_pii_findings = redact_dlp_text(question)

        # Read the active synthesis contract from the prompt registry so an
        # env/registry override (e.g. a task-specific table-only contract)
        # actually takes effect here instead of being silently ignored.
        try:
            from core.prompt_registry import get_prompt as _get_prompt
            system_prompt = _get_prompt("synthesizer.system")
        except Exception:  # реестр промптов недоступен — берём встроенный контракт синтеза
            system_prompt = SYSTEM_ANSWER
        if completion_nonce:
            # Appended to the system prompt rather than to one of the three
            # user-prompt branches, so every synthesis shape carries it.
            system_prompt = system_prompt + completion_marker_instruction(
                completion_nonce, _COMPLETION_DECLARATIONS
            )
        if local_critique is not None and not artifacts:
            cite = citation_token_for_referent(local_critique)
            raw_target = (local_critique.analysis_target_excerpt or "").strip()
            # Cap oversized targets; keep the turn bounded.
            _max_target = 12_000
            truncated = False
            if len(raw_target) > _max_target:
                raw_target = raw_target[:_max_target]
                truncated = True
            safe_target, _, _ = redact_dlp_text(raw_target)
            safe_target = (
                safe_target.replace("</analysis_target>", "")
                .replace("<analysis_target", "&lt;analysis_target")
            )
            directive_raw = (local_critique.directive_excerpt or question).strip()
            safe_directive, _, _ = redact_dlp_text(directive_raw)
            safe_directive = safe_directive.replace("</directive>", "")
            show_only = is_show_only_directive(directive_raw)
            trunc_note = (
                "\n[note] analysis_target truncated for length.\n"
                if truncated
                else ""
            )
            show_only_line = (
                "Show-only: do not offer further actions or help.\n"
                if show_only
                else ""
            )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{profile_block}"
                f"<directive>\n{safe_directive}\n</directive>\n\n"
                f'<analysis_target untrusted="true">\n'
                f"{safe_target}{trunc_note}"
                f"</analysis_target>\n\n"
                f"<allowed_target_citation>{cite}</allowed_target_citation>\n\n"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"{show_only_line}"
                "Answer using the Output Contract. Critique only the "
                "analysis_target. Cite target-descriptive claims with the "
                "allowed_target_citation token. Do not use [general-knowledge] "
                "or [memory:*] for this turn. Do not claim the object is missing."
            )
            system_prompt = system_prompt + "\n" + LOCAL_CRITIQUE_SYSTEM_ADDENDUM
        elif artifacts:
            from core.evidence_budget import (
                MEMORY_BLOCK_LABEL,
                apply_total_budget,
                rebuild_trimmed_memory,
                total_trims,
            )
            raw_blocks: list[tuple[str, str]] = []
            for label, art in artifacts.items():
                formatted = format_artifact(
                    art["tool"], art["output"], question=question
                )
                raw_blocks.append((label, formatted))

            # Long-term memory competes for the SAME budget as the evidence
            # collected this cycle. It used to be concatenated into the prompt
            # outside `apply_total_budget` entirely, which made recollection
            # structurally untrimmable while the freshly read file — almost
            # always the largest block — was cut first. Observed consequence:
            # a months-old "Bug fixed …" record survived the trim that removed
            # the code proving it, and the agent reported a fixed bug as
            # current. Memory is demoted (`trim_first`): it is spent before any
            # fresh evidence is touched, whatever the relative sizes.
            memory_label = MEMORY_BLOCK_LABEL
            while memory_label in artifacts:      # never shadow a real artifact
                memory_label += "_"
            memory_payload = long_term_block.strip()
            if memory_payload:
                raw_blocks.append((memory_label, memory_payload))

            trimmed_blocks, was_trimmed = apply_total_budget(
                raw_blocks, trim_first_labels={memory_label}
            )

            memory_trimmed = False
            # None = memory was not trimmed, so every retrieved record is still
            # in the prompt and citable. A set = only these survived.
            surviving_memory_ids: set[str] | None = None
            evidence_pairs: list[tuple[str, str]] = []
            for lbl, content in trimmed_blocks:
                if lbl != memory_label:
                    evidence_pairs.append((lbl, content))
                    continue
                memory_trimmed = content != memory_payload
                memory_block = content
                if memory_trimmed:
                    _records = getattr(self, "_last_persistent_records", [])
                    memory_block, surviving_memory_ids = rebuild_trimmed_memory(
                        content,
                        memory_payload,
                        list(
                            zip(
                                [rec.id for rec in _records],
                                self.memory_record_lines(_records),
                                strict=True,
                            )
                        ),
                    )
                long_term_block = f"{memory_block}\n\n" if memory_block else ""

            if was_trimmed:
                # Parsed once; feeds both the trim event and the starvation
                # detector below (review round #286).
                _trims = total_trims(trimmed_blocks)
                self.log.log(
                    "evidence_budget_trim",
                    {
                        "labels": [lbl for lbl, _ in trimmed_blocks],
                        # NOTE: since memory joined the budget this total
                        # includes the memory block — not comparable with
                        # totals logged before that change.
                        "total_chars": sum(len(c) for _, c in trimmed_blocks),
                        # Which side paid for the overflow — the whole point of
                        # the demotion rule, and unreadable from `labels` alone.
                        # `memory_chars` separates "memory was there and
                        # survived" from "there was no memory at all", which a
                        # bare `memory_trimmed: False` cannot express.
                        "memory_trimmed": memory_trimmed,
                        "memory_chars": len(memory_payload),
                        # What actually reached the model. Without these two,
                        # `persistent_memory_inject` (which fires BEFORE the
                        # budget) is the only memory signal in the trace, and
                        # it reports records that may have been trimmed away
                        # entirely — a reader reconstructing the run from the
                        # log would count memory the model never saw.
                        "memory_chars_kept": len(long_term_block.strip()),
                        "memory_ids_kept": sorted(surviving_memory_ids)
                        if surviving_memory_ids is not None
                        else None,
                        # Per-block cut sizes, parsed back from the trim
                        # notices — without them a starved block is invisible
                        # in the trace (MIR-073).
                        "trims": [
                            {"label": lbl, "kept": kept, "original": orig}
                            for lbl, kept, orig in _trims
                        ],
                    },
                )
                # MIR-073: the planner chose these sources; if the budget
                # squeezed one to a sliver, that is two deciders contradicting
                # each other — journal it on the existing disagreement channel
                # instead of continuing as if nothing happened. Logging only,
                # per the operator's sensor policy.
                try:
                    from core.subsystem_disagreement import (
                        detect_budget_starvation,
                    )
                    for _ev in detect_budget_starvation(
                        _trims,
                        planned_labels=set(artifacts.keys()),
                        memory_label=memory_label,
                    ):
                        self.log.log("subsystem_disagreement", _ev)
                except Exception as _sd_exc:
                    # A broken detector must not break the turn — but its
                    # failure must not be invisible either (review round
                    # #286, same rule as verification_explained_failed).
                    try:
                        self.log.log(
                            "subsystem_disagreement_error",
                            {
                                "error_type": type(_sd_exc).__name__,
                                "error": str(_sd_exc)[:300],
                            },
                        )
                    except Exception:
                        pass

            blocks: list[str] = [
                f'<evidence source="{lbl}">\n{content}\n</evidence>'
                for lbl, content in evidence_pairs
            ]
            evidence = "\n\n".join(blocks)
            # A record the trim removed must not stay on the citable list: the
            # synthesizer would cite text it never saw and the verifier would
            # book it as cited-but-unmatched.
            allowed_citations_block = format_allowed_citations_block(
                self.last_provenance, memory_ids=surviving_memory_ids
            )
            scope_notice = file_scope_notice(question, artifacts)
            file_scope_block = (
                "<file_scope_notice>\n"
                f"{scope_notice}\n"
                "Do not claim any unverified path exists or was read.\n"
                "</file_scope_notice>\n\n"
                if scope_notice
                else ""
            )

            warnings = [
                f"{label}: {'; '.join(art['issues'])}"
                for label, art in artifacts.items()
                if art.get("issues")
            ]
            warnings_block = (
                "<validator_notes>\n" + "\n".join(warnings) + "\n</validator_notes>\n\n"
                if warnings
                else ""
            )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"{allowed_citations_block}"
                f"{file_scope_block}"
                f"{evidence}\n\n"
                f"{warnings_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "Answer using the Output Contract from the system instructions. "
                "Maintain continuity with any prior turns shown in conversation_history."
                # Only offered when the block is actually in the prompt: the
                # budget can drop it entirely, and describing how to cite an
                # absent block is an invitation to cite nothing.
                + (
                    " If long_term_memory contains a relevant record you may "
                    "cite it with source label [memory:<record_id>]."
                    if long_term_block.strip()
                    else ""
                )
            )
        else:
            # No tools were called — either the planner judged this a
            # general-knowledge question, OR a follow-up answerable from
            # conversation_history / long_term_memory alone, OR re-planning
            # exhausted (failure_history present). The Output Contract
            # still applies in every case.
            extra_guidance = ""
            if failure_history:
                extra_guidance = (
                    "Re-planning was exhausted. Use the <failure_context> "
                    "block to write an honest Conclusion: state plainly that "
                    "the agent could not collect evidence, list what was "
                    "tried (one bullet per attempt), and put the unmet "
                    "information need under Unverified. Cite each fact as "
                    "[general-knowledge] when relying on prior knowledge."
                )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "No <evidence> blocks are provided. If conversation_history or "
                "long_term_memory covers the answer, cite those sources verbatim "
                "(use [memory:<record_id>] for long_term_memory entries); "
                "otherwise answer from general knowledge with [general-knowledge] "
                "as the source label. Follow the Output Contract from the system instructions."
                + (f" {extra_guidance}" if extra_guidance else "")
            )

        # Record whether the active contract is the generic Conclusion/Facts
        # prose contract. When a task-specific/structured contract replaced it,
        # the verifier must not flag the answer as malformed for lacking the
        # generic headers (output-contract priority).
        self._synthesis_expects_contract_headers = output_contract_requires_headers(
            system_prompt
        )

        # Defence-in-depth: the prompt is now built from redacted artifacts
        # and a redacted question. One more pass catches anything we missed
        # (e.g. a secret or PII hiding in planner_reasoning).
        safe_user_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        from core.host_tools_context import _build_host_tools_block, host_tools_relevant
        # Local critique must not pull host_tools context. Otherwise inject only
        # when the turn is actually about host tools (name / task word / an
        # effect tool was used) — so .env paths don't ride along on unrelated
        # questions (LPF-001 iteration 1b).
        _tools_used = [a.get("tool") for a in artifacts.values() if isinstance(a, dict)]
        if local_critique is None and host_tools_relevant(
            f"{question}\n{planner_reasoning}", _tools_used
        ):
            host_block = _build_host_tools_block()
            # Reference context ONLY — NOT evidence. Wrapping host tools as an
            # <evidence> block used to flip the synthesizer into "answer STRICTLY
            # from evidence" mode (disabling the general-knowledge path) and
            # produced fabricated [tool:host_tools] citations the verifier could
            # never match (LPF-001). A <host_environment> block is reference-only,
            # must not be cited, and never switches off general-knowledge answering.
            if host_block:
                host_context = (
                    "\n\n<host_environment>\n"
                    + host_block.strip()
                    + "\nThis lists programs installed on the machine. It is reference "
                    "context, NOT a source: never cite it (no [tool:...] or [source] "
                    "label), and its presence does not make an answer 'grounded'. "
                    "When you actually write a script for one of these tools, state the "
                    "EXACT run command from above in the Facts section."
                    + "\n</host_environment>"
                )
                safe_user_prompt = safe_user_prompt + host_context
        _on_token = getattr(self, "_stream_on_token", None)
        if _on_token is not None:
            return _active_llm.stream_complete(
                system=system_prompt, user=safe_user_prompt,
                temperature=0.5, on_token=_on_token,
            )
        return _active_llm.complete(
            system=system_prompt, user=safe_user_prompt, temperature=0.5
        )
