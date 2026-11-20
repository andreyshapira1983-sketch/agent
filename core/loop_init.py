"""Construction of ``AgentLoop``: the constructor body moved verbatim out of ``core/loop.py``."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.approval import ApprovalProvider
from core.assumption_registry import AssumptionRegistry, AssumptionStore
from core.evidence import ProvenanceChain
from core.evidence_classes import SelfAnalysisDecision
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.llm import LLM
from core.logger import TraceLogger
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_router import ModelRole, ModelRouter
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.referent_resolver import ReferentDecision
from core.replan import DEFAULT_MAX_REPLAN_ATTEMPTS, ReplanTrigger
from core.role_router import RoleContext, RoleRouter
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
)
from core.source_ranker import SourceRankingReport
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from core.step_repetition import StepRepetitionTracker
from core.termination_guard import TerminationGuard
from core.user_profile import UserProfile, UserProfileStore
from tools.base import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.actuation_gateway import GatewayPath
    from core.memory_echo_antibody import MemoryWriteRegistry
    from core.replan import ReplanPolicy


class AgentLoopInit:
    """Loop constructor: lays out the passed arguments as fields."""

    def __init__(  # noqa: PLR0913, PLR0917 — flat: depth 2, no branching state to follow
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
        assumption_store: AssumptionStore | None = None,
        gateway_dry_run: bool = False,
        gateway_path: GatewayPath = "repl",
        experience_retrieval: bool = True,
        episodic_replay: bool = True,
        durable_writes: frozenset[str] | None = None,
        # Appended at the tail on purpose: the constructor is called positionally, so inserting mid-list would silently shift same-typed arguments.
        pending_clarification_path: Path | None = None,
        causal_store: Any = None,
        observe_before_answer: bool = False,
    ):
        self.registry = registry
        self.policy = policy
        self.gateway_dry_run = gateway_dry_run
        self.gateway_path = gateway_path
        # Memory permissions, instance-scoped and fixed for this agent's life.
        # experience_retrieval: inject episodic/procedural memory into planning.
        # episodic_replay: let the fast path serve a stored answer.
        # durable_writes: None = all sinks; a frozenset is an allowlist (unknown sinks denied).
        # The audit and dry-run brakes outrank it (see `_durable_learning_suppressed`).
        self.experience_retrieval = experience_retrieval
        self.episodic_replay = episodic_replay
        self.durable_writes = durable_writes
        self.suppress_durable_learning_writes = False
        # Audit / read-only brake: when True there are NO durable learning writes and 'agent-auto' persistent/semantic writes are frozen.
        # Independent of `suppress_durable_learning_writes` (saved/restored by dry-run) so a cycle cannot lift it mid-run.
        self.audit_read_only = False
        # Records whether this audit toggle installed the agent-auto freeze, so turning audit off never lifts an AGENT_FREEZE_AUTO_MEMORY env brake.
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
        #: Where a clarification parks its question so a LATER PROCESS can pick it up.
        self.pending_clarification_path = pending_clarification_path
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
        self.causal_store = causal_store
        self.knowledge_auto_write = bool(knowledge_auto_write)
        # When None, escalated actions are blocked outright (safe default).
        self.approval_provider = approval_provider
        # Replan policy: per-FailureType budgets, planner advice, forbidden (tool, args) pairs.
        # The older `max_replan_attempts` is still honoured; an explicit policy that disagrees with it is an error.
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
                max_replan_attempts != DEFAULT_MAX_REPLAN_ATTEMPTS  # noqa: PLR1714 — the split guard compares this body to core/loop.py symbol for symbol
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
        self.max_replan_attempts = self.replan_policy.max_total_replans
        # Compensation registry (LIFO): successful tool calls with a non-noop CompensationPlan are appended; `rollback()` applies the last one.
        # In-memory on purpose: rollback must happen within the session that made the change.
        from core.compensation import CompensationPlan  # avoid top-level cycle

        self._CompensationPlanCls = CompensationPlan
        self.compensation_log: list[CompensationPlan] = []
        # Per-cycle list of secrets found in tool outputs; reset by `run()`, passed to the synthesizer.
        self._cycle_findings: list[dict[str, Any]] | None = None
        # Per-step slot where `_execute_step` stores a `ReplanTrigger` when it returns None; `run()` consumes and clears it.
        self._last_step_failure: ReplanTrigger | None = None
        # Current attempt number, set by `run()` each iteration so `_execute_step` can stamp triggers.
        self._current_attempt: int = 1
        # Synthesis streaming callback set by `run()` from `on_token`; reset to None at the end of each cycle.
        self._stream_on_token: Any = None
        # Set when every synthesis attempt failed and the turn fell back to a degraded answer.
        self._last_synth_degraded: bool = False
        # Provenance scratch state: records injected by `_retrieve_persistent`, folded into the Evidence chain; `last_provenance` is set at the end of `run()`.
        self._last_persistent_records: list[Any] = []
        self._last_episode_records: list[Any] = []
        self._last_procedure_records: list[Any] = []
        # Fast-path cache: best episodic match from the last _retrieve_experience_memory call.
        self._last_best_similar_episode: Any = None  # EpisodeRecord | None
        self._last_best_similar_score: float = 0.0
        # Step-repetition tracker; replaced per `run()` call.
        self._step_repetition: StepRepetitionTracker = StepRepetitionTracker()
        # Termination guard; replaced per `run()` call.
        self._termination_guard: TerminationGuard = TerminationGuard()
        self.last_provenance: ProvenanceChain = ProvenanceChain()
        self.last_role_context: RoleContext = self.role_router.route("")
        # `verifier_enabled=False` skips the annotation pass and returns the draft verbatim.
        self.verifier_enabled = bool(verifier_enabled)
        # User Profile store and last-known profile snapshot.
        self.user_profile_store = user_profile_store
        self.last_user_profile: UserProfile | None = None
        # Assumption Registry store and last-run snapshot.
        self.assumption_store = assumption_store
        self.last_assumptions: AssumptionRegistry | None = None
        self._run_assumptions_current: AssumptionRegistry | None = None
        # `clarification_enabled=False` skips the heuristic ambiguity check (integration tests only).
        self.clarification_enabled = bool(clarification_enabled)
        # Clarification gate: when the loop is STUCK (replan exhausted), prepend minimal clarifying questions to the failure answer.
        # `False` skips it. Deterministic, no LLM, no I/O.
        self.clarification_gate_enabled = bool(clarification_gate_enabled)
        # Last run stuck signal for autonomous runtime parity.
        self.last_replan_exhausted: bool = False
        # The gate answered with a question instead of work; the runtime reads this.
        self.last_answer_was_clarification: bool = False
        # Operational Design Domain check: when enabled, an out-of-domain request is refused or escalated BEFORE planning.
        self.odd_enabled = bool(odd_enabled)
        # Cheap-path planner gate: trivial no-tool input skips the planner and synthesises directly; `False` forces the planner.
        self.cheap_path_enabled = bool(cheap_path_enabled)
        # After a successful step batch, one more planning round with what was seen (core/observation_round.py). Off by default.
        self.observe_before_answer = bool(observe_before_answer)
        from core.verifier import VerificationReport as _VR

        self.last_verification: _VR | None = None
        # Applicability verdict from `evaluate_evidence_support`; keeps "no evidence" and "evidence not required" distinguishable.
        self.last_evidence_support: Any = None
        self.last_referent_decision: ReferentDecision | None = None
        # Whether this turn was a conversational correction / request to explain the agent's previous reply; decided per turn.
        self.last_self_analysis: SelfAnalysisDecision | None = None
        # Trust metadata over the Evidence chain.
        self.last_confidence_vector: Any = None  # reads a memory record
        # Source ranking is logged; the Ranker-to-Output Policy uses it to cap confidence for unsuitable realtime sources.
        self.last_source_ranking: SourceRankingReport | None = None
        # Source registry: catalog view over the same chain (source records plus first-pass claims).
        self.last_source_registry: SourceRegistry = SourceRegistry()
        self.last_knowledge_pipeline: KnowledgePipelineResult | None = None
