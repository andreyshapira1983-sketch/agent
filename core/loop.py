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

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing import Literal as _Literal
from core.replan import FailureType as ReplanCode  # single source of truth

from core.approval import ApprovalProvider
from core.data_classifier import DataClass, SourceHint, classify
from core.evidence import (
    Evidence,
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_tool_result,
    make_evidence,
)
from core.ids import new_id
from core.llm import LLM
from core.logger import TraceLogger
from core.memory import WorkingMemory
from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWriteDecision,
    MemoryWritePolicy,
)
from core.memory_echo_antibody import make_event

if TYPE_CHECKING:
    from core.memory_echo_antibody import MemoryWriteEvent, MemoryWriteRegistry
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
from core.output_policy import apply_ranker_output_policy
from core.low_evidence_policy import (
    evaluate_low_evidence_policy,
    is_evidence_expected,
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
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner, PlannerOutput
from core.policy import PolicyGate
from core.actuation_gateway import GatewayPath
from core.injection_guard import annotate_suspicious, scan_for_injection

# Thread-local storage for per-step replan triggers.
# _execute_step writes here instead of self._last_step_failure so that
# parallel worker threads each own an isolated slot — no shared-state race.
_step_trigger_tls: threading.local = threading.local()

# Tools that read only from the local workspace (trusted boundary).
# Injection guard is skipped for these — their output cannot be injected
# by an external adversary and false-positives degrade signal quality.
_TRUSTED_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "list_dir",
    "diff_file",
    "run_tests",
    "read_logs",
})
from core.redaction import (
    collect_pii_findings,
    redact_dlp_text,
    redact_payload,
    scan,
)
from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelBudgetExceeded
from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    run_synthesizer_ladder,
)
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult, RememberFn
from core.user_profile import UserProfile, UserProfileStore, profile_to_prompt_block
from core.assumption_registry import (  # Layer 5
    AssumptionRegistry,
    AssumptionStore,
    extract_from_plan,
    extract_from_question,
)
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.confidence_gate import ConfidenceGate
from core.reasoning_action_check import check_reasoning_actions
from core.role_router import RoleContext, RoleRouter
from core.step_repetition import StepRepetitionTracker
from core.termination_guard import TerminationGuard
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
    episode_from_agent_cycle,
    format_experience_context,
)
from core.source_ranker import SourceRankingReport, rank_chain
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from core.task_complexity import can_skip_planner
from tools.base import ToolRegistry
from tools.file_read import MAX_BYTES as FILE_READ_MAX_BYTES
from core.loop_helpers import (  # noqa: F401 -- re-exported
    DEFAULT_MAX_REPLAN_ATTEMPTS,
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    _ANSWER_CITATION_RE,
    _VERIF_MARKER_RE,
    _strip_verification_markers,
    _to_text,
    format_human_response,
    new_trace_id,
)
from core.loop_methods import AgentLoopExtractedMethods
from core.loop_methods2 import AgentLoopExtractedMethods2


# Maps tool names to data_classifier source hints. Drives the per-tool
# default DataClass (file_read -> private, web_search -> public, …).
_TOOL_SOURCE_HINTS: dict[str, SourceHint] = {
    "file_read": "file",
    "web_search": "web",
}


# Default attempt budget for re-planning. Two replans (3 attempts total)
# is the tradeoff: enough room to recover from a typo or a flaky source,
# not enough to mask a fundamentally wrong plan as "just one more try".


# ReplanCode is an alias for FailureType (core/replan.py) — single source of truth.
# Imported above. No local definition needed.


@dataclass(frozen=True)
class ReplanTrigger:
    """Structured record of one failed PlanStep.

    Lives in two places:
      - `AgentLoop._last_step_failure` — scratch slot set by
        `_execute_step` at every early-return; the parent loop drains it
        right after the step returns None.
      - `failure_history` inside `run()` — the cumulative list across
        attempts, formatted into a `<replan_context>` block fed to the
        planner.

    `arguments` are stored verbatim from the failed step. They are
    redacted at log time by `TraceLogger` and again before the planner
    prompt is sent, so a credential pasted in by the LLM into a tool
    argument cannot leak via the replan path either.
    """

    code: ReplanCode
    step_id: str
    tool_name: str | None
    arguments: dict[str, Any]
    reason: str
    attempt: int


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


class AgentLoop(AgentLoopExtractedMethods2, AgentLoopExtractedMethods):
    """Runs a single agent cycle.

    MVP-3: the planner is an LLM. The CLI only supplies the question plus an
    optional file hint. The planner picks which tools (if any) to call; the
    Executor runs the plan and the Synthesizer produces the Output Contract.
    """

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
        memory_write_registry: "MemoryWriteRegistry | None" = None,
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
        replan_policy: "ReplanPolicy | None" = None,
        verifier_enabled: bool = True,
        clarification_enabled: bool = True,
        clarification_gate_enabled: bool = True,
        odd_enabled: bool = True,
        cheap_path_enabled: bool = True,
        user_profile_store: UserProfileStore | None = None,
        assumption_store: AssumptionStore | None = None,  # Layer 5
        gateway_dry_run: bool = False,
        gateway_path: GatewayPath = "repl",
    ):
        self.registry = registry
        self.policy = policy
        self.gateway_dry_run = gateway_dry_run
        self.gateway_path = gateway_path
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
        # Post-verifier confidence gate (Berkeley MAST 2025, Horvitz 1999).
        # Constructed once; threshold/min_total can be overridden by tests.
        self._confidence_gate: ConfidenceGate = ConfidenceGate()
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
        """
        # Store streaming callback so _synthesize() can pick it up without
        # changing its signature (which is called from multiple paths).
        self._stream_on_token = on_token
        self._cycle_findings = []
        self.last_replan_exhausted = False
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None
        durable_learning_writes = not self._durable_learning_suppressed()

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
        # Layer 2→5 bridge: if the store already has assumptions for this run_id
        # (e.g. a previous failed attempt in the same session), restore them so
        # they are not duplicated and the prompt block stays accurate.
        try:
            if self.assumption_store is not None:
                _existing = self.assumption_store.load_by_run(
                    getattr(self.log, "trace_id", "")
                )
                if _existing:
                    _run_assumptions.restore_from_store(_existing)
                    self.log.log("assumptions_restored", {
                        "run_id": getattr(self.log, "trace_id", ""),
                        "count": len(_existing),
                    })
        except Exception:
            pass  # Restore must never abort the run.
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
            class _NoOpCP:  # noqa: N801
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
        # Falls back silently to the default LLM if for_task() raises.
        try:
            _task_planner_llm = self.model_router.for_task(
                ModelRole.PLANNER, user_question, escalation=deep_escalation
            )
            _task_synth_llm = self.model_router.for_task(
                ModelRole.SYNTHESIZER, user_question, escalation=deep_escalation
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
                    "planner_model": _planner_model,
                    "synth_model": _synth_model,
                    "route_reason": _route_reason,
                },
            )
        except Exception:
            _task_planner_llm = None
            _task_synth_llm = None

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

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
            not local_critique_active
            and not file_hint
            and not user_question.strip().startswith(":")
            and _fp_ep is not None
            and _fp_score >= 0.85
            and _fp_ep.answer_quality_score >= 0.70
            and _fp_ep.full_answer
            and not _fp_ep.tools_used
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
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=_fp_ep.full_answer,
                tools_used=[],
                source_labels=[f"memory:{_fp_ep.id}"],
                verified_chunks=1,
                unverified_chunks=0,
                replan_exhausted=False,
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
        multi_file = self._prepare_multi_file_review(user_question, file_hint=file_hint)
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
                            self._count_failures(failure_history)
                        ),
                    },
                )

            failure_context = self._format_replan_context(
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
                planner_out = self._force_file_hint_read_when_explicit(
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
            except Exception:
                pass

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
            try:
                # `persistent_block` was built from a small set of records
                # in `_retrieve_persistent`; we replay that retrieval cheaply
                # by re-asking the store for the keyword match.
                for rec in self._last_persistent_records:
                    chain.add(
                        evidence_from_memory_record(
                            record_id=rec.id,
                            content=rec.content,
                            source=getattr(rec, "source", None),
                            created_at=getattr(rec, "created_at", None),
                        )
                    )
            except Exception:
                # Defence-in-depth: chain assembly must NEVER abort the
                # run. A malformed record stays out of the chain but the
                # loop completes normally.
                pass

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
                except Exception:
                    pass

        # Store the chain on the agent so tests / future Verifier code
        # can consult it after `run()` returns.
        self.last_provenance = chain

        # MAST FM-3.1 — premature completion risk: empty evidence chain
        # on a question whose phrasing demanded tools. Observational.
        try:
            _pc = self._termination_guard.check_completion(
                question=user_question,
                chain_size=len(chain),
                had_any_artifacts=bool(artifacts),
            )
            if _pc is not None:
                self.log.log(
                    "premature_completion_risk", _pc.to_log_payload()
                )
        except Exception:
            pass

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
                source_store=self.source_registry_store if durable_learning_writes else None,
                remember=self._knowledge_remember_batch() if durable_learning_writes else None,
                auto_write_memory=(
                    self.knowledge_auto_write if durable_learning_writes else False
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
            except Exception:
                _synth_llm = _task_synth_llm
        _saved_on_token = getattr(self, "_stream_on_token", None)

        def _do_synthesize(_attempt: "SynthAttempt") -> str:
            # Retries must not double-stream tokens: only the first attempt may
            # stream to the console; adapted/retry attempts render silently and
            # the final answer is returned normally.
            if _attempt.index > 0:
                self._stream_on_token = None
            return self._synthesize(
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

        try:
            _ladder = run_synthesizer_ladder(
                _do_synthesize,
                build_degraded_answer=build_degraded_synthesis_answer,
                on_event=self.log.log,
                fatal_types=(ModelBudgetExceeded,),
            )
            draft_answer = _ladder.answer
            self._last_synth_degraded = _ladder.degraded
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
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
                verify as _verify,
            )

            report = _verify(
                answer=draft_answer,
                chain=chain,
                user_question=user_question,
                **self._verification_receipt_kwargs(),
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
            except Exception:
                pass

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
            except Exception:
                pass
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

            # Berkeley MAST 2025 / Horvitz 1999 — post-verifier confidence
            # gate. Observational only: the loop may still ship the answer,
            # but a `low_confidence_gate` event is emitted when the
            # verified/cited mass is below threshold so operators can
            # detect over-confident-but-unsupported answers.
            try:
                _gate = self._confidence_gate.evaluate(report)
                if _gate.triggered:
                    self.log.log(
                        "low_confidence_gate", _gate.to_log_payload()
                    )
            except Exception:
                pass

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
                            self._count_failures(failure_history)
                        ),
                    },
                )

                failure_context = self._format_replan_context(
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
                planner_out = self._force_file_hint_read_when_explicit(
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
                report = _verify(
                answer=draft_answer,
                chain=chain,
                user_question=user_question,
                **self._verification_receipt_kwargs(),
            )
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
                        self.source_registry_store if durable_learning_writes else None
                    ),
                    remember=(
                        self._knowledge_remember_batch()
                        if durable_learning_writes
                        else None
                    ),
                    auto_write_memory=(
                        self.knowledge_auto_write if durable_learning_writes else False
                    ),
                )
                source_registry = knowledge_result.registry
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

        policy_result = apply_ranker_output_policy(
            answer=answer,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            answer = policy_result.answer
            self.log.log("output_policy", policy_result.to_log_payload())

        # B-1 Clarification Gate — режим переспроса. When the loop is STUCK
        # (replan exhausted == loop_suspected), the mature response is to ASK,
        # not to keep building. Prepend the gate's minimal clarifying questions
        # to the honest answer so the operator can narrow the frame. Pure and
        # deterministic (no LLM, no I/O); best-effort so it can never take down
        # the response path.
        if replan_exhausted and self.clarification_gate_enabled:
            try:
                from core.clarification_gate import clarification_for_replan_exhausted
                _clarify = clarification_for_replan_exhausted()
                _clarify_prompt = _clarify.prompt()
                if _clarify_prompt and _clarify_prompt not in answer:
                    self.log.log("clarification_gate", _clarify.to_dict())
                    answer = f"{_clarify_prompt}\n\n{answer}"
            except Exception:
                pass

        # Low-evidence enforcement: if the verifier's verdict
        # distribution is severely below threshold (few verified, many
        # unverified, long enough draft), replace the long polished
        # answer with a deterministic short reply that lists ONLY the
        # verified claims and explicitly states "insufficient data".
        # The informational ConfidenceGate already logs the warning;
        # this is the structural enforcement paired with it.
        if self.last_verification is not None:
            try:
                _ranking = self.last_source_ranking
                # Evidence is only "expected" for factual / realtime questions.
                # A pure reasoning or design question produces an empty evidence
                # chain and carries no realtime intent; suppressing it would
                # throw away a legitimate answer (even a costly Opus one). A
                # generative coding turn (role=programmer, output_style="diff")
                # is the same class: it delivers NEW code/docstring/diff that the
                # factual verifier cannot match verbatim against the file it read
                # as input, so the gate would delete exactly what was requested.
                # Default to expecting evidence whenever we cannot tell, so the
                # gate stays fully in force for factual and realtime queries.
                _chain_empty = bool(
                    getattr(self.last_verification, "chain_was_empty", False)
                )
                _realtime = (
                    bool(getattr(_ranking, "realtime_required", True))
                    if _ranking is not None
                    else True
                )
                _evidence_expected = is_evidence_expected(
                    role=getattr(self.last_role_context, "role", ""),
                    chain_was_empty=_chain_empty,
                    realtime_required=_realtime,
                )
                _le = evaluate_low_evidence_policy(
                    answer=answer,
                    report=self.last_verification,
                    question=user_question,
                    evidence_expected=_evidence_expected,
                )
                if _le.triggered:
                    self.log.log(
                        "low_evidence_truncation", _le.to_log_payload()
                    )
                    answer = _le.answer
            except Exception:
                # Defence-in-depth: truncation must NEVER take down
                # the loop. A failed evaluation falls back to the
                # original answer the user would otherwise have got.
                pass

        # Strip internal verification markers before user-facing output.
        # Must happen AFTER output_policy which needs [verified:...] markers.
        answer = _strip_verification_markers(answer)

        file_scope_notice = self._file_scope_notice(user_question, artifacts)
        if file_scope_notice and file_scope_notice not in answer:
            answer = f"{file_scope_notice}\n\n{answer}"
            self.log.log(
                "file_scope_notice",
                {
                    "notice": file_scope_notice,
                    "artifact_labels": list(artifacts.keys()),
                },
            )

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
            except Exception:
                pass

        verification = self.last_verification
        if durable_learning_writes:
            weak_chunks = 0
            if verification:
                weak_chunks = (
                    verification.subagent_asserted_chunks
                    + verification.cited_but_unmatched_chunks
                    + verification.receipt_missing_chunks
                    + verification.topic_supported_but_claim_unverified_chunks
                )
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
            )
        else:
            self.log.log(
                "durable_learning_writes_skipped",
                {"reason": "dry_run"},
            )

        # Layer 4 — update user profile from this interaction.
        if durable_learning_writes and self.user_profile_store is not None:
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
            durable_learning_writes
            and self.assumption_store is not None
            and _run_assumptions.new_assumptions
        ):
            try:
                self.assumption_store.save_many(_run_assumptions.new_assumptions)
            except Exception:
                pass  # Store failure must never abort the run.

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

    @staticmethod
    def _count_failures(history: list[ReplanTrigger]) -> dict[str, int]:
        """Return {failure_code: count} over the cumulative history.

        Used both by the audit log payload (`replan_attempt`) and the
        synthesizer prompt, so it's worth having one place that knows
        how to summarise the list.
        """
        counts: dict[str, int] = {}
        for t in history:
            counts[t.code] = counts.get(t.code, 0) + 1
        return counts

    @staticmethod
    def _format_replan_context(
        failure_history: list[ReplanTrigger],
        attempt: int,
        max_attempts: int,
        advice: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """Build the <replan_context> block fed to the planner.

        Empty string when there's nothing to replan against (first attempt
        and no prior failures). Otherwise a compact XML block listing each
        failed step with its code, tool, arguments, and reason — exactly
        the shape the planner's system prompt has been told to consume.

        MVP-12 additions:
          - `advice` is the `ReplanDecision.advice_for_planner` string
            composed from per-FailureType budgets. It tells the planner
            what KIND of correction to make.
          - `forbidden_actions` lists (tool, args_json) pairs the planner
            must NOT propose again. Surfaced inline so the LLM can read
            and respect it; the sanitiser also enforces it as defence in
            depth.

        We do NOT redact secrets here because:
          - tool arguments came from the planner's previous output, which
            was itself redacted before going to the LLM, so they cannot
            contain raw secrets that the planner hadn't already seen;
          - `LLMPlanner.plan` runs `redact_text` over the assembled user
            prompt one more time as a safety net.
        """
        if not failure_history and not advice and not forbidden_actions:
            return ""
        lines = [
            f"<replan_context attempt=\"{attempt}\" "
            f"max_attempts=\"{max_attempts}\">"
        ]
        if failure_history:
            lines.append(
                f"  Previous attempts produced {len(failure_history)} failed "
                f"step(s). Pick a different approach."
            )
            for trig in failure_history:
                args_compact = json.dumps(trig.arguments, ensure_ascii=False)
                lines.append(
                    f"  - attempt={trig.attempt} "
                    f"code={trig.code} "
                    f"tool={trig.tool_name or '(none)'} "
                    f"arguments={args_compact}"
                )
                lines.append(f"    reason: {trig.reason}")
        if advice:
            lines.append("  <advice>")
            for advice_line in advice.splitlines():
                if advice_line.strip():
                    lines.append(f"    {advice_line}")
            lines.append("  </advice>")
        if forbidden_actions:
            lines.append(
                "  <forbidden>  # do NOT propose these exact (tool, arguments) pairs"
            )
            for tool, args_json in forbidden_actions:
                lines.append(f"    - tool={tool} arguments={args_json}")
            lines.append("  </forbidden>")
        lines.append("</replan_context>")
        return "\n".join(lines)

    # ---------- phase implementations ----------


    def _check_clarification(self, user_question: str) -> "ClarificationResult":
        """Run the Clarification Policy (§3) — pure heuristic, no LLM."""
        from core.clarification_policy import ClarificationResult, check_clarification
        return check_clarification(user_question)

    def _check_operational_domain(self, user_question: str) -> "DomainResult":
        """Run the Operational Design Domain gate (§7 ODD) — pure, no LLM."""
        from core.operational_domain import DomainResult, check_operational_domain
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
                for turn in self.memory.recent_turns(5):
                    prior_turns.append(
                        PriorTurnRef(
                            turn_id=turn.id,
                            session_id=session_id,
                            question=turn.question,
                            answer=turn.answer,
                            timestamp=turn.timestamp,
                        )
                    )
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
        except Exception:
            pass

    def _force_file_hint_read_when_explicit(
        self,
        planner_out: PlannerOutput,
        *,
        question: str,
        file_hint: str | None,
    ) -> PlannerOutput:
        if not file_hint:
            return planner_out
        if any(src.get("tool") == "file_read" for src in planner_out.sources):
            return planner_out
        if not self._explicitly_requests_hinted_file(question):
            return planner_out
        sources = list(planner_out.sources)
        sources.append(self._file_hint_source(file_hint))
        warnings = list(planner_out.warnings)
        warnings.append(
            "explicit file-read request used --file hint because planner selected no file_read"
        )
        return PlannerOutput(
            reasoning=(
                f"{planner_out.reasoning} "
                "Kernel added read-only file_read for explicit hinted-file request."
            ).strip(),
            sources=sources,
            raw_response=planner_out.raw_response,
            warnings=warnings,
        )

    @staticmethod
    def _explicitly_requests_hinted_file(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            phrase in text
            for phrase in (
                "прочитай файл",
                "прочти файл",
                "прочитать файл",
                "проанализируй файл",
                "анализ файла",
                "файл задания",
                "task file",
                "read the file",
                "read this file",
                "analyze the file",
            )
        )

    @staticmethod
    def _file_hint_source(file_hint: str) -> dict[str, Any]:
        return {
            "tool": "file_read",
            "arguments": {"path": file_hint},
            "label": f"file:{file_hint}",
            "expected_outcome": "Non-empty UTF-8 text from the hinted file.",
        }

    def _prepare_multi_file_review(
        self,
        question: str,
        *,
        file_hint: str | None,
    ) -> dict[str, Any]:
        requested_paths = self._extract_path_mentions(question)
        if len(requested_paths) < 2 or not self._is_file_review_request(question):
            return {"kind": "none"}

        explicit_mode = self._is_explicit_multi_file_mode(question)
        if file_hint and not explicit_mode:
            hint_norm = self._normalize_path_mention(file_hint)
            extra_paths = [
                path
                for path in requested_paths
                if self._normalize_path_mention(path) != hint_norm
            ]
            if extra_paths:
                available = file_hint
                extras = ", ".join(extra_paths)
                return {
                    "kind": "refusal",
                    "message": (
                        "Regular --file mode only permits the hinted file. "
                        f"Available evidence: {available}. "
                        f"Requested additional files were not reviewed: {extras}. "
                        "Use :ingest-source for additional files or explicit "
                        "multi-file review mode."
                    ),
                    "requested_paths": requested_paths,
                    "file_hint": file_hint,
                    "extra_paths": extra_paths,
                }
            return {"kind": "none"}

        if not explicit_mode and file_hint:
            return {"kind": "none"}
        if not explicit_mode and not self._looks_like_multi_file_review_without_hint(question):
            return {"kind": "none"}

        root = self._file_read_workspace_root()
        if root is None:
            return {
                "kind": "refusal",
                "message": (
                    "Multi-file review is unavailable because file_read is not "
                    "registered in this agent session."
                ),
                "requested_paths": requested_paths,
            }

        valid_sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_targets: set[str] = set()
        rejected: list[dict[str, str]] = []
        for raw_path in requested_paths:
            item = self._validate_user_file_path(raw_path, workspace=root)
            if item["ok"] is not True:
                rejected.append({
                    "path": raw_path,
                    "reason": str(item["reason"]),
                })
                warnings.append(f"{raw_path}: {item['reason']}")
                continue
            target_key = str(item["target"]).casefold()
            if target_key in seen_targets:
                warnings.append(f"{raw_path}: duplicate path skipped")
                continue
            seen_targets.add(target_key)
            rel_path = item["relative_path"].as_posix()
            valid_sources.append({
                "tool": "file_read",
                "arguments": {"path": rel_path},
                "label": f"file:{rel_path}",
                "expected_outcome": "Non-empty UTF-8 text from the explicitly mentioned file.",
            })

        if not valid_sources:
            details = "; ".join(
                f"{item['path']}: {item['reason']}" for item in rejected
            ) or "no valid files were mentioned"
            return {
                "kind": "refusal",
                "message": (
                    "Multi-file review could not start because no valid "
                    f"workspace files passed preflight. {details}."
                ),
                "requested_paths": requested_paths,
                "rejected": rejected,
            }

        self.log.log(
            "multi_file_review_preflight",
            {
                "requested_paths": requested_paths,
                "accepted_paths": [src["arguments"]["path"] for src in valid_sources],
                "rejected": rejected,
                "warnings": warnings,
            },
        )
        return {
            "kind": "forced",
            "sources": valid_sources,
            "warnings": warnings,
            "rejected": rejected,
            "reasoning": (
                "Kernel explicit multi-file review: read only the user-mentioned "
                "workspace files that passed strict path preflight. "
                + ("Rejected/skipped: " + "; ".join(warnings) if warnings else "")
            ),
        }

    @staticmethod
    def _is_file_review_request(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "review",
                "read",
                "compare",
                "check",
                "проверь",
                "проверить",
                "прочитай",
                "прочти",
                "прочитать",
                "сравни",
                "сравнить",
                "проанализируй",
                "анализ",
            )
        )

    @staticmethod
    def _is_explicit_multi_file_mode(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "explicit multi-file",
                "multi-file review",
                "multi file review",
                "multi-file read",
                "multi file read",
                "режим нескольких файлов",
                "несколько файлов",
                "многофайлов",
            )
        )

    @staticmethod
    def _looks_like_multi_file_review_without_hint(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "сравни",
                "сравнить",
                "compare",
                "review these files",
                "read these files",
            )
        )

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

    def _validate_user_file_path(self, raw_path: str, *, workspace: Path) -> dict[str, Any]:
        cleaned = raw_path.strip().strip("\"'")
        normalized = cleaned.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", normalized):
            return {"ok": False, "reason": "absolute path escapes workspace"}
        path = Path(normalized)
        if any(part == ".." for part in path.parts):
            return {"ok": False, "reason": "path traversal is not allowed"}
        target = path.resolve() if path.is_absolute() else (workspace / path).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            return {"ok": False, "reason": "absolute path escapes workspace"}
        if not target.exists():
            return {"ok": False, "reason": "missing file"}
        if target.is_dir():
            return {"ok": False, "reason": "directories require explicit ingestion command"}
        if not target.is_file():
            return {"ok": False, "reason": "not a regular file"}
        size = target.stat().st_size
        if size > FILE_READ_MAX_BYTES:
            return {
                "ok": False,
                "reason": f"file too large ({size} bytes > {FILE_READ_MAX_BYTES})",
            }
        rel_path = target.relative_to(workspace)
        return {
            "ok": True,
            "target": target,
            "relative_path": rel_path,
        }

    # ------------------------------------------------------------------
    # Parallel step execution helpers
    # ------------------------------------------------------------------

    def _run_step_parallel(
        self, step: PlanStep
    ) -> tuple[PlanStep, dict[str, Any] | None, "ReplanTrigger | None"]:
        """Thread-safe wrapper: runs _execute_step and returns (step, outcome, trigger).

        Clears the thread-local trigger slot before calling _execute_step so that
        each thread starts with a clean slate. Can be submitted safely to a
        ThreadPoolExecutor worker.
        """
        _step_trigger_tls.step_trigger = None
        outcome = self._execute_step(step)
        trigger = getattr(_step_trigger_tls, "step_trigger", None)
        _step_trigger_tls.step_trigger = None  # consume
        return step, outcome, trigger

    def _execute_steps_parallel(
        self, steps: list["PlanStep"]
    ) -> list[tuple["PlanStep", dict[str, Any] | None, "ReplanTrigger | None"]]:
        """Execute a list of plan steps, running independent steps in parallel.

        Steps whose ``preconditions`` list is empty (the common case — the
        planner currently never fills it) are all considered independent and
        run concurrently in a ThreadPoolExecutor. Steps that declare a
        precondition referencing another step in the same batch are run
        sequentially after the parallel group completes.

        Returns results in plan order: [(step, outcome, trigger), ...].
        """
        if not steps:
            return []
        if len(steps) == 1:
            return [self._run_step_parallel(steps[0])]

        # Partition: parallel = no intra-batch dependencies; sequential = rest.
        step_ids = {s.id for s in steps}
        parallel = [s for s in steps if not any(pc in step_ids for pc in s.preconditions)]
        sequential = [s for s in steps if any(pc in step_ids for pc in s.preconditions)]

        results: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]] = []

        if len(parallel) > 1:
            max_workers = min(len(parallel), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._run_step_parallel, step): step
                    for step in parallel
                }
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for step in parallel:
                results.append(self._run_step_parallel(step))

        # Sequential steps follow in plan order.
        for step in sequential:
            results.append(self._run_step_parallel(step))

        # Re-sort to plan order so callers process artifacts in a stable sequence.
        order_map = {s.id: i for i, s in enumerate(steps)}
        results.sort(key=lambda r: order_map.get(r[0].id, 9999))
        return results

    def _execute_step(self, step: PlanStep) -> dict[str, Any] | None:
        """Run a single PlanStep through Act -> Policy -> Tool -> Verify.

        Working Memory short-circuit: if (tool, arguments) is already in the
        artifact cache, we skip Policy + Tool + Verify and reuse the cached
        output. Read-only tools are deterministic enough that this is safe
        within a single session.

        Returns artifact dict on success, None on hard failure. On failure
        the method also writes a `ReplanTrigger` to `_step_trigger_tls.step_trigger`
        so the parent loop can decide whether to re-plan. Using thread-local
        storage makes this safe to call from a ThreadPoolExecutor worker.
        """
        # Clear the scratch slot so the caller never sees a stale trigger
        # from a previous step within the same attempt.
        _step_trigger_tls.step_trigger = None

        tool_name = step.action_spec.get("tool_name")
        source_label = step.action_spec.get("source_label") or f"step:{step.id}"
        arguments = step.action_spec.get("arguments", {})

        # MAST FM-1.3 — track (tool, args) repetition across the whole run.
        # Logged only the first time the count crosses the threshold so the
        # event remains a single signal, not a per-replan stream.
        _tracker = getattr(self, "_step_repetition", None)
        if _tracker is not None and tool_name:
            _rep = _tracker.observe(tool_name, arguments)
            if _rep is not None:
                self.log.log("step_repetition_detected", _rep)

        # Memory cache short-circuit
        if self.memory is not None and tool_name:
            cached = self.memory.cache_lookup(tool_name, arguments)
            if cached is not None:
                self.log.log(
                    "memory_cache_hit",
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "label": cached["label"],
                        "stored_at_turn": cached["turn_index"],
                    },
                )
                return {
                    "label": cached["label"],
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": cached["output"],
                    "issues": ["served from working-memory cache"],
                }

        action = Action(
            step_id=step.id,
            type=step.action_spec["type"],
            tool_name=tool_name,
            parameters=arguments,
            side_effects="read" if tool_name else "none",
        )
        self.log.log("act", action, source_label=source_label)

        from core.actuation_gateway import (
            ActuationGateway,
            is_effectful_tool,
            simulate_output,
        )

        if is_effectful_tool(tool_name or "", arguments, self.registry):
            from core.budget_kill_switch import BudgetKillSwitch, default_path

            ws = self._file_read_workspace_root()
            kill_switch = getattr(self, "gateway_kill_switch", None)
            if kill_switch is None and ws is not None:
                kill_switch = BudgetKillSwitch(path=default_path(ws))
            gateway = ActuationGateway(
                self.policy,
                path=self.gateway_path,
                dry_run=self.gateway_dry_run,
                kill_switch=kill_switch,
                budget_snapshot=getattr(self, "gateway_budget_snapshot", None),
                readiness_blockers=getattr(self, "gateway_readiness_blockers", ()),
                check_readiness=bool(getattr(self, "gateway_check_readiness", False)),
            )
            gw = gateway.evaluate(action, registry=self.registry)
            self.log.log("gateway_decision", gw.to_log_payload())
            # G4: durable gateway decision receipt (kind="gateway"). Never raises;
            # observation only — does not change the verdict handling below.
            from core.tool_receipts import (  # local import: avoid cycles
                receipt_context as _gw_receipt_context,
                record_gateway_receipt as _record_gateway_receipt,
            )

            with _gw_receipt_context(
                trace_id=self.log.trace_id,
                path=self.gateway_path,
                workspace=self._file_read_workspace_root(),
            ):
                _record_gateway_receipt(gw)
            if gw.outcome == "simulate":
                return {
                    "label": source_label,
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": simulate_output(tool_name or "", arguments),
                    "issues": ["gateway simulate — effect not executed"],
                    "data_class": "internal",
                }
            if gw.outcome == "deny":
                err = ErrorObject(
                    source="gateway",
                    code="policy_blocked",
                    message=f"Action blocked: {', '.join(gw.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "deny",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Gateway denied: {', '.join(gw.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None
            if gw.outcome == "block":
                err = ErrorObject(
                    source="gateway",
                    code="gateway_blocked",
                    message=f"Hard stop: {', '.join(gw.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "block",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Gateway blocked: {', '.join(gw.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None
            decision = gw.policy
            assert decision is not None
            self.log.log("policy", decision)
        else:
            # Policy Gate — pre-execution checkpoint (non-effectful tools)
            decision = self.policy.check(action)
            self.log.log("policy", decision)

            if decision.decision == "deny":
                err = ErrorObject(
                    source="policy",
                    code="policy_blocked",
                    message=f"Action blocked: {', '.join(decision.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "deny",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Policy gate denied: {', '.join(decision.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None

        if decision.decision == "escalate":
            # PolicyGate flagged a risky action. Bring in the human via the
            # ApprovalProvider; if no provider is wired, the safe default
            # is to refuse — exactly the pre-MVP-6 behaviour.
            verdict = self._request_approval(
                action=action,
                step=step,
                source_label=source_label,
                policy_decision=decision,
                arguments=arguments,
            )
            if verdict != "approve":
                # Map the approval-side verdict back to a replan code
                # so the planner can pick a safer alternative (e.g.
                # swap an irreversible action for a read-only one).
                code: ReplanCode = (
                    "approval_deny" if verdict == "deny"
                    else "approval_abort" if verdict == "abort"
                    else "approval_unavailable"
                )
                # PolicyGate would have denied an unknown tool before we
                # got here, so the registry lookup is safe.
                risk = (
                    self.registry.get(tool_name).risk if tool_name else "unknown"
                )
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code=code,
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Approval gate returned '{verdict}' for risk={risk}",
                    attempt=self._current_attempt,
                )
                return None

        if action.type != "tool_call":
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Unsupported action type: {action.type}",
                attempt=self._current_attempt,
            )
            return None  # not used in MVP-2

        # Tool execution
        result = self._call_tool(action)
        if result.status != "success":
            raw_error = result.error or "tool execution failed"
            # Detect file-absence specifically: FileNotFoundError (or
            # IsADirectoryError) means the file simply does not exist on
            # disk. This is a hard stop — the planner must NOT retry the
            # same path, search the web, or recreate the file from
            # general knowledge. Use a dedicated FailureType so the
            # replan policy communicates this clearly.
            _file_absent_prefixes = ("FileNotFoundError:", "IsADirectoryError:")
            if any(raw_error.startswith(p) for p in _file_absent_prefixes):
                replan_code: ReplanCode = "file_not_found"
            else:
                replan_code = "tool_error"
            err = ErrorObject(
                source=action.tool_name or "tool",
                code=replan_code,
                message=raw_error,
                severity="error",
                recoverable=False,
                context={"tool_call_id": result.tool_call_id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code=replan_code,
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=raw_error,
                attempt=self._current_attempt,
            )
            return None

        # Tool Result Validation (delegates to the tool itself)
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        is_ok, issues = tool.validate_output(result.output)
        self.log.log(
            "verify",
            {
                "step_id": step.id,
                "ok": is_ok,
                "issues": issues,
                "source_label": source_label,
            },
        )
        if not is_ok:
            err = ErrorObject(
                source="verifier",
                code="verify_failed",
                message=f"Tool Result Validation failed: {', '.join(issues) or 'unspecified'}",
                severity="error",
                recoverable=True,
                context={"step_id": step.id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="verify_failed",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Validation rejected output: {'; '.join(issues) or 'no detail'}",
                attempt=self._current_attempt,
            )
            return None

        # --- MVP-12: structured-failure reclassification ----------------------
        # Two outcomes pass validate_output but still need a re-plan with a
        # specific advice: empty web_search results and shell_exec timeouts.
        # Detecting them HERE (not inside the tools) keeps the tool contracts
        # stable while letting the ReplanPolicy advise the planner per case.
        if action.tool_name == "web_search" and isinstance(result.output, list) and len(result.output) == 0:
            self.log.log(
                "error",
                ErrorObject(
                    source="web_search",
                    code="web_empty",
                    message="web_search returned zero hits — query needs reformulation",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="web_empty",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="web_search returned 0 results; reformulate the query",
                attempt=self._current_attempt,
            )
            return None
        if action.tool_name == "shell_exec" and isinstance(result.output, dict) and result.output.get("timed_out"):
            self.log.log(
                "error",
                ErrorObject(
                    source="shell_exec",
                    code="timeout",
                    message="shell_exec hit its timeout — output truncated, exit_code unknown",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="timeout",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="shell_exec timed out; reduce scope or pick a faster path",
                attempt=self._current_attempt,
            )
            return None

        # --- Safety pipeline (§7 / MVP-7) ---------------------------------
        # 1. Classify the tool output.
        # 2. Emit secret_detected when the classifier returns SECRET.
        # 3. Deep-redact the output BEFORE it reaches artifacts, memory
        #    cache, or the synthesizer prompt. From this point on the loop
        #    only handles redacted text.
        #
        # Outer guard: classification / redaction helpers should never raise,
        # but if they do (e.g. a corrupted output string), we capture the
        # exception as an "unknown" replan trigger so the plan can retry
        # rather than crashing the entire cycle.
        try:
            source_hint: SourceHint = _TOOL_SOURCE_HINTS.get(
                action.tool_name or "", "tool_output"
            )
            flat_output = _to_text(result.output)
            # Trusted internal tools (our own logs, files, diffs, test output)
            # live inside the trusted boundary. A bare mention of a credential
            # word like "api_key" in that content is not a leaked secret and
            # must not quarantine the agent's own evidence — so the soft
            # keyword layer is disabled for them. Real credential SHAPES
            # (regex spans) are still detected and classified SECRET.
            keyword_secrets = (action.tool_name or "") not in _TRUSTED_INTERNAL_TOOLS
            cls_result = classify(
                flat_output, source=source_hint, keyword_secrets=keyword_secrets
            )
            self.log.log(
                "data_classified",
                {
                    "label": source_label,
                    "tool": action.tool_name,
                    "class": cls_result.cls.value,
                    "source": cls_result.source,
                    "reasons": cls_result.reasons,
                },
            )

            # --- Injection Guard (§2 Adversarial Defence) ------------------
            # Scan tool output for indirect prompt-injection patterns BEFORE
            # the content reaches the synthesizer prompt.  Blocked content is
            # dropped and triggers a replan; suspicious content is annotated
            # and passed through with a trust warning.
            # Internal workspace tools (file_read, list_dir, …) are exempt:
            # their content originates inside the trusted boundary and
            # scanning them produces false-positives with no security benefit.
            if action.tool_name in _TRUSTED_INTERNAL_TOOLS:
                inj = None
            else:
                inj = scan_for_injection(flat_output)
            if inj is not None and inj.verdict != "clean":
                self.log.log(
                    "injection_" + inj.verdict,
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        **inj.to_log_payload(),
                    },
                )
            if inj is not None and inj.is_blocked:
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="injection_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=(
                        "Tool output blocked by injection guard "
                        f"({len(inj.findings)} finding(s)). "
                        "Try a different source or query."
                    ),
                    attempt=self._current_attempt,
                )
                return None
            if inj is not None and inj.verdict == "suspicious":
                # Wrap the output with a trust-warning annotation so the
                # synthesizer knows the content may be adversarial.
                annotated = annotate_suspicious(flat_output, source_label)
                result = result.model_copy(update={"output": annotated})
                flat_output = annotated
            # ---------------------------------------------------------------

            if cls_result.cls == DataClass.SECRET:
                findings = scan(flat_output)
                kinds = sorted({f.kind for f in findings})
                self.log.log(
                    "secret_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                # Redact SECRET output before it reaches the planner,
                # synthesizer, or memory cache.
                safe_output = redact_payload(result.output)

                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }
            elif cls_result.cls == DataClass.SENSITIVE:
                findings = collect_pii_findings(flat_output)
                kinds = sorted({f"pii-{f.kind}" for f in findings})
                self.log.log(
                    "sensitive_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                safe_output = redact_payload(result.output)

                # Memory write — cache the REDACTED artifact for future turns
                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    # MVP-14.1: surfaced so the evidence factory can build a
                    # typed `Evidence` (source_id often depends on an argument
                    # like `path` or `url`).
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }

            # PUBLIC / INTERNAL / SECRET-flagged data: cache original output
            # and return it to the planner. For SECRET data the loop already
            # emitted a `secret_detected` log event above; the output is
            # returned as-is so the planner can still act on it.
            if self.memory is not None and action.tool_name:
                self.memory.cache_store(
                    tool_name=action.tool_name,
                    arguments=arguments,
                    output=result.output,
                    label=source_label,
                )

            return {
                "label": source_label,
                "tool": action.tool_name,
                "arguments": action.parameters,
                "output": result.output,
                "issues": issues,
                "data_class": cls_result.cls.value,
            }

        except Exception as exc:  # noqa: BLE001
            # Last-resort guard: if any postprocessing step (classify,
            # redact, cache_store) raises unexpectedly, capture it as an
            # "unknown" replan trigger rather than crashing the loop.
            self.log.log(
                "error",
                ErrorObject(
                    source="loop",
                    code="unknown",
                    message=f"Postprocessing error in _execute_step: {type(exc).__name__}: {exc}",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Postprocessing error: {type(exc).__name__}: {exc}",
                attempt=self._current_attempt,
            )
            return None

    def _call_tool(self, action: Action) -> ToolResult:
        assert action.tool_name is not None
        tool = self.registry.get(action.tool_name)
        call = ToolCall(
            action_id=action.id,
            tool_name=action.tool_name,
            arguments=action.parameters,
        )
        self.log.log("tool_call", call)
        from core.tool_receipts import receipt_context

        workspace = self._file_read_workspace_root()
        with receipt_context(
            trace_id=self.log.trace_id,
            path="repl",
            workspace=workspace,
        ):
            result = tool.invoke(call)
        self.log.log("tool_result", result, status=result.status, latency_ms=result.latency_ms)
        # MVP-11 Compensation capture: a successful tool call may carry a
        # `compensation_plan` block in its structured output. The agent
        # owns the plan registry from here on — `rollback()` reads from
        # this list, in LIFO order, and applies the plan inside the
        # workspace root sandbox.
        self._capture_compensation_plan(result)
        return result

    def _capture_compensation_plan(self, result: ToolResult) -> None:
        """Extract + register a `compensation_plan` from tool output, if any.

        Only success results are inspected. The plan is logged as a
        `compensation_registered` event so an audit can match every
        applied rollback to the tool call that produced it.
        """
        if result.status != "success" or not isinstance(result.output, dict):
            return
        raw = result.output.get("compensation_plan")
        if not isinstance(raw, dict):
            return
        # Skip noop plans — they pollute :rollback semantics and the
        # audit log. The plan is still IN the tool_result output, so
        # an analyst can see read-only commands also produced one.
        actions = raw.get("actions") or []
        if all(a.get("kind") == "noop" for a in actions):
            return
        from core.compensation import CompensationPlan  # avoid import cycle

        plan = CompensationPlan.from_dict(raw)
        self.compensation_log.append(plan)
        self.log.log(
            "compensation_registered",
            {
                "plan_id": plan.id,
                "tool_name": plan.tool_name,
                "description": plan.description,
                "action_count": len(plan.actions),
                "tool_call_id": result.tool_call_id,
            },
        )

    # ---------- approval ----------

    def _request_approval(
        self,
        action: Action,
        step: PlanStep,
        source_label: str,
        policy_decision: PolicyDecision,
        arguments: dict[str, Any],
    ) -> _Literal["approve", "deny", "abort", "unavailable"]:
        """Bridge between the PolicyGate's `escalate` verdict and the human.

        Emits two trace events — `approval_request` then `approval_decision`
        — and turns the decision into a control-flow signal for the loop:
          - "approve" -> caller continues with the tool call
          - anything else -> caller bails out, no tool_call is logged
        """
        if self.approval_provider is None:
            err = ErrorObject(
                source="approval",
                code="approval_unavailable",
                message=(
                    "Action escalated but no approval provider is configured. "
                    f"Reasons: {', '.join(policy_decision.reasons)}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "policy_decision_id": policy_decision.id,
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            return "unavailable"

        # Tool must exist — PolicyGate would have returned deny otherwise.
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        # Mirror the PolicyGate: ask the tool for its argument-aware
        # risk so the approval prompt reports what the gate actually
        # decided on, not the static class fallback.
        effective_risk = tool.risk_for(arguments or {})
        request = ApprovalRequest(
            action_id=action.id,
            step_id=step.id,
            tool_name=action.tool_name,
            arguments=arguments,
            risk=effective_risk,
            reasons=list(policy_decision.reasons),
            summary=(
                f"Tool '{action.tool_name}' wants to execute "
                f"with risk={effective_risk}."
            ),
            policy_decision_id=policy_decision.id,
        )
        self.log.log("approval_request", request)

        decision = self.approval_provider.request(request)
        self.log.log("approval_decision", decision)

        if decision.decision == "approve":
            return "approve"

        err = ErrorObject(
            source="approval",
            code=f"approval_{decision.decision}",  # approval_deny | approval_abort
            message=(
                f"Approval not granted ({decision.decision}): "
                f"{'; '.join(decision.reasons) or 'no reason given'}"
            ),
            severity="error",
            recoverable=False,
            context={
                "action_id": action.id,
                "approval_request_id": request.id,
                "approval_decision_id": decision.id,
                "responder": decision.responder,
                "source_label": source_label,
            },
        )
        self.log.log("error", err)
        return decision.decision

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
            for f in cycle_findings:
                lines.append(
                    f"- label={f['label']} kinds={f['kinds']} "
                    f"count={f['count']} (kernel-redacted)"
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

        system_prompt = SYSTEM_ANSWER
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
            system_prompt = SYSTEM_ANSWER + "\n" + LOCAL_CRITIQUE_SYSTEM_ADDENDUM
        elif artifacts:
            from core.evidence_budget import apply_total_budget
            raw_blocks: list[tuple[str, str]] = []
            for label, art in artifacts.items():
                formatted = self._format_artifact(
                    art["tool"], art["output"], question=question
                )
                raw_blocks.append((label, formatted))

            # Apply total evidence budget — trim largest artifact first
            trimmed_blocks, was_trimmed = apply_total_budget(raw_blocks)
            if was_trimmed:
                self.log.log(
                    "evidence_budget_trim",
                    {
                        "labels": [lbl for lbl, _ in trimmed_blocks],
                        "total_chars": sum(len(c) for _, c in trimmed_blocks),
                    },
                )

            blocks: list[str] = [
                f'<evidence source="{lbl}">\n{content}\n</evidence>'
                for lbl, content in trimmed_blocks
            ]
            evidence = "\n\n".join(blocks)
            allowed_citations_block = self._format_allowed_citations_block(
                self.last_provenance
            )
            file_scope_notice = self._file_scope_notice(question, artifacts)
            file_scope_block = (
                "<file_scope_notice>\n"
                f"{file_scope_notice}\n"
                "Do not claim any unverified path exists or was read.\n"
                "</file_scope_notice>\n\n"
                if file_scope_notice
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
                "Maintain continuity with any prior turns shown in conversation_history. "
                "If long_term_memory contains a relevant record you may cite it "
                "with source label [memory:<record_id>]."
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

        # Defence-in-depth: the prompt is now built from redacted artifacts
        # and a redacted question. One more pass catches anything we missed
        # (e.g. a secret or PII hiding in planner_reasoning).
        safe_user_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        from core.planner import _build_host_tools_block  # lazy import — avoids circular
        # Local critique must not pull host_tools as synthetic "evidence".
        if local_critique is None:
            host_block = _build_host_tools_block()
            # Inject host tools as a synthetic evidence block so the model treats
            # the paths as verified context (the model answers strictly from evidence).
            if host_block:
                host_evidence = (
                    "\n\n<evidence source=\"host_tools\">\n"
                    + host_block.strip()
                    + "\nWhen a script was written for one of these tools, state the EXACT "
                    "run command from above in the Facts section so the user knows how to execute it."
                    + "\n</evidence>"
                )
                safe_user_prompt = safe_user_prompt + host_evidence
        _on_token = getattr(self, "_stream_on_token", None)
        if _on_token is not None:
            return _active_llm.stream_complete(
                system=system_prompt, user=safe_user_prompt,
                temperature=0.5, on_token=_on_token,
            )
        return _active_llm.complete(
            system=system_prompt, user=safe_user_prompt, temperature=0.5
        )

    @staticmethod
    def _citation_for_evidence(ev: Evidence) -> str | None:
        source_id = ev.source_id
        if ev.kind == "file" and source_id.startswith("file:"):
            body = source_id[len("file:"):]
            return f"[file:{body}]"
        if ev.kind == "web_page" and source_id.startswith("web_page:"):
            body = source_id[len("web_page:"):]
            return f"[web:{body}]"
        if ev.kind == "web_search_hit" and source_id.startswith("web_search:"):
            body = source_id[len("web_search:"):]
            return f"[search:{body}]"
        if ev.kind == "test_result" and source_id.startswith("test_result:"):
            body = source_id[len("test_result:"):]
            return f"[test:{body}]"
        if ev.kind == "log_event" and source_id.startswith("log_event:"):
            body = source_id[len("log_event:"):]
            return f"[log:{body}]"
        if ev.kind == "shell_output" and source_id.startswith("shell_output:"):
            body = source_id[len("shell_output:"):]
            return f"[shell:{body}]"
        if ev.kind == "tool_output" and source_id.startswith("tool_output:"):
            body = source_id[len("tool_output:"):]
            return f"[tool:{body}]"
        if ev.kind == "diff_preview" and source_id.startswith("diff_preview:"):
            body = source_id[len("diff_preview:"):]
            return f"[diff:{body}]"
        if ev.kind == "memory":
            return f"[memory:{source_id}]"
        if ev.kind == "user_explicit":
            return "[user]"
        return None

    @classmethod
    def _format_allowed_citations_block(cls, chain: ProvenanceChain) -> str:
        if not chain.evidences:
            return ""
        lines = ["<allowed_citations>"]
        seen: set[str] = set()
        for ev in chain.evidences:
            token = cls._citation_for_evidence(ev)
            if token is None or token in seen:
                continue
            seen.add(token)
            lines.append(
                f"- {token} kind={ev.kind} source_id={ev.source_id}"
            )
        lines.append("</allowed_citations>")
        return "\n".join(lines) + "\n\n" if len(lines) > 2 else ""

    @staticmethod
    def _format_artifact(tool_name: str | None, output: Any, *, question: str = "") -> str:
        """Render a tool output into a stable string the LLM can ground on.

        File content is passed through :func:`core.evidence_budget.budget_file_content`
        which applies the per-artifact character budget and performs intent-aware
        extraction: instead of blindly returning the first N chars, the most
        question-relevant paragraphs are selected (Realtime Intent Fix).
        """
        if tool_name == "web_search" and isinstance(output, list):
            if not output:
                return "(no results)"
            lines: list[str] = []
            for r in output:
                title   = r.get("title") or "(no title)"
                url     = r.get("url") or ""
                snippet = r.get("snippet") or ""
                source  = r.get("source") or "duckduckgo"
                lines.append(f"- {title}")
                lines.append(f"  url: {url}")
                if snippet:
                    lines.append(f"  snippet: {snippet}")
                lines.append(f"  provider: {source}")
            return "\n".join(lines)
        if tool_name == "file_read" and isinstance(output, str):
            from core.evidence_budget import budget_file_content
            return budget_file_content(output, question=question)
        if tool_name == "list_dir" and isinstance(output, str):
            return output
        # Fallback: stringify whatever came back.
        return str(output)

    @classmethod
    def _file_scope_notice(
        cls,
        question: str,
        artifacts: dict[str, dict[str, Any]],
    ) -> str:
        actual_paths = [
            label[len("file:"):]
            for label, art in artifacts.items()
            if label.startswith("file:") and art.get("tool") == "file_read"
        ]
        if not actual_paths:
            return ""
        actual_norms = {cls._normalize_path_mention(path) for path in actual_paths}
        requested_paths = cls._extract_path_mentions(question)
        missing = [
            path
            for path in requested_paths
            if cls._normalize_path_mention(path) not in actual_norms
        ]
        if not missing:
            return ""
        actual = ", ".join(actual_paths)
        unverified = ", ".join(missing)
        return (
            f"Evidence scope: I only have evidence for {actual}. "
            f"I did not verify {unverified}."
        )

    @staticmethod
    def _extract_path_mentions(text: str) -> list[str]:
        pattern = re.compile(
            r"(?P<path>"
            r"(?:[A-Za-z]:[\\/])?"
            r"(?:/)?"
            r"(?:\.{1,2}[\\/])?"
            r"(?:[A-Za-z0-9_.-]+[\\/])*"
            r"[A-Za-z0-9_.-]+\."
            r"(?:py|md|txt|json|yml|yaml|pdf)"
            r")",
            flags=re.IGNORECASE,
        )
        seen: set[str] = set()
        paths: list[str] = []
        for match in pattern.finditer(text):
            path = match.group("path").rstrip(".,;:!?)\"]}'")
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    @staticmethod
    def _normalize_path_mention(path: str) -> str:
        out = path.strip().strip("\"'")
        out = out.replace("/", "\\")
        while out.startswith(".\\"):
            out = out[2:]
        return out.casefold()
