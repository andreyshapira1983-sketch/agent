"""Сборка ``AgentLoop`` — конструктор, вырезанный из ``core/loop.py`` дословно.

Правило оператора: «разбирай большие файлы на компактные подключаемые модули».
Двенадцатый кусок раскола, и первый, вынутый не ради размера, а ради ответа на
вопрос «чем является этот файл»: `core/loop.py` — оркестратор цикла §3, а
сборка объекта оркестровкой не является. 34 параметра и 65 присваиваний
описывают, ИЗ ЧЕГО агент состоит, а не КАК он ведёт ход.

Вместе с конструктором сюда уехали 30 импортов, нужных только ему, — в цикле
они были ровно тем, что делало его шапку непрочитываемой.

Комментарии перенесены целиком, и их тут много (99 строк на 65 присваиваний):
это единственное место, где записано, чем является каждое поле и почему у него
такое значение по умолчанию. Ужимать их, «оптимизируя» размер файла, —
удалять знание, оставляя код.

Композиционный корень проекта — `app/bootstrap.py:build_agent`; здесь только
раскладка переданного по полям, без принятия решений о том, что подставить.

Класс подмешивается в ``AgentLoop``: `__init__` разрешается по MRO, других
конструкторов среди примесей нет.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.approval import ApprovalProvider
from core.assumption_registry import AssumptionRegistry, AssumptionStore
from core.evidence import ProvenanceChain
from core.evidence_classes import SelfAnalysisDecision
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.llm import LLM
from core.logger import TraceLogger
from core.loop_helpers import DEFAULT_MAX_REPLAN_ATTEMPTS
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_router import ModelRole, ModelRouter
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.referent_resolver import ReferentDecision
from core.replan import ReplanTrigger
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
    """Конструктор цикла: раскладка переданного по полям.

    Отдельный класс, а не свободная функция, потому что `__init__` обязан
    находиться по MRO — `AgentLoop` наследует его так же, как остальные
    примеси, и для вызывающего ничего не меняется.
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
