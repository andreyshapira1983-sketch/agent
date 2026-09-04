"""Agent construction: tool registry, policy, memory stores, model router."""
from __future__ import annotations

from pathlib import Path

from cli.parsers import _env_bool
from core.approval import ApprovalProvider
from core.assumption_registry import AssumptionStore  # Layer 5
from core.budget_ledger import BudgetLedger
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_echo_antibody import MemoryWriteRegistry
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelUsageLedger
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
)
from core.source_registry_store import SourceRegistryStore
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH as _DEFAULT_RUNTIME_TASKS_PATH
from core.user_profile import UserProfileStore
from tools.base import ToolRegistry
from tools.current_time import CurrentTimeTool
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.journal_append import JournalAppendTool
from tools.lesson_provenance_tool import LessonProvenanceTool
from tools.list_dir import ListDirTool
from tools.memory_bank import MemoryBankTool
from tools.memory_recall import MemoryRecallTool
from tools.python_probe import PythonProbeTool
from tools.read_logs import ReadLogsTool
from tools.rss_fetch import RssFetchTool
from tools.run_tests import RunTestsTool
from tools.semantic_scholar_search import SemanticScholarSearchTool
from tools.shell_exec import ShellExecTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool

DEFAULT_PERSISTENT_PATH = Path("data") / "persistent_memory.jsonl"
DEFAULT_SOURCE_REGISTRY_PATH = Path("data") / "source_registry.jsonl"
# Re-exported from `core.task_queue`, which owns the store: one definition,
# and the daemon can read it without importing this module.
DEFAULT_RUNTIME_TASKS_PATH = _DEFAULT_RUNTIME_TASKS_PATH
DEFAULT_RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
DEFAULT_MODEL_USAGE_PATH = Path("data") / "model_usage.jsonl"
DEFAULT_BUDGET_LEDGER_PATH = Path("data") / "budget_ledger.jsonl"
DEFAULT_EPISODIC_MEMORY_PATH = Path("data") / "episodic_memory.jsonl"
DEFAULT_PROCEDURAL_MEMORY_PATH = Path("data") / "procedural_memory.jsonl"
DEFAULT_MEMORY_CONSOLIDATION_PATH = Path("data") / "memory_consolidation.jsonl"
DEFAULT_USER_PROFILE_PATH = Path("data") / "user_profile.jsonl"
DEFAULT_ASSUMPTIONS_PATH = Path("data") / "assumptions.jsonl"  # Layer 5
#: Нижняя ступень причинной лестницы: наблюдение переживает ход.
DEFAULT_CAUSAL_PATH = Path("data") / "causal_observations.jsonl"
#: Where a clarification parks the question it asked ABOUT, so the operator's
#: reply is added to that question instead of replacing it. Survives the
#: process because the agent that asks and the agent that hears the answer
#: are not the same object — measured, see core/pending_clarification.py.
DEFAULT_PENDING_CLARIFICATION_PATH = Path("data") / "pending_clarification.json"


def build_agent(
    workspace: Path,
    with_memory: bool,
    with_persistent: bool = True,
    persistent_path: Path | None = None,
    approval_provider: ApprovalProvider | None = None,
    with_experience: bool | None = None,
    experience_retrieval: bool = True,
    episodic_replay: bool = True,
    durable_writes: frozenset[str] | None = None,
) -> AgentLoop:
    """Assemble the production agent for one entry point.

    Memory is controlled along four INDEPENDENT axes, because "has stores",
    "may read them", "may replay an answer from them" and "may write durable
    state" are genuinely different permissions:

    - ``with_memory``      — session/working memory.
    - ``with_persistent``  — persistent/semantic memory (+ source registry,
                             user profile, assumptions).
    - ``with_experience``  — episodic/procedural/consolidation stores. Defaults
                             to ``with_memory`` so every existing caller keeps
                             its current profile; pass it explicitly to give a
                             path experience memory without working memory.
    - ``experience_retrieval`` / ``episodic_replay`` / ``durable_writes``
                           — read, replay and write permissions, forwarded to
                             the loop (see ``AgentLoop.__init__``).

    ``durable_writes`` is an ALLOWLIST of durable sinks: ``None`` means "all"
    (the interactive default), a frozenset permits exactly those sinks and
    denies everything else, and ``frozenset()`` denies all durable writes. The
    audit and dry-run brakes outrank it absolutely. It is **instance-scoped**:
    fixed for the whole life of the returned agent, with deliberately no
    per-run API yet.
    """
    if with_experience is None:
        with_experience = with_memory
    trace_id = new_trace_id()
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(ListDirTool(workspace_root=workspace))
    registry.register(WebSearchTool())
    registry.register(FileWriteTool(workspace_root=workspace))
    # Journal append tool - appends to journal files, needs only workspace root
    registry.register(JournalAppendTool(workspace_root=workspace))
    registry.register(ShellExecTool(workspace_root=workspace))
    # Лаборатория: замер собственной среды экспериментом (см. tools/python_probe.py).
    registry.register(PythonProbeTool())
    # Прибор причинного происхождения уроков (см. tools/lesson_provenance_tool.py).
    registry.register(LessonProvenanceTool(workspace_root=str(workspace)))
    # Pure clock primitive — keeps the agent from guessing today's date.
    registry.register(CurrentTimeTool())
    # MVP-13.1 — self-repair diagnostic primitives.
    registry.register(RunTestsTool(workspace_root=workspace))
    registry.register(ReadLogsTool(workspace_root=workspace, live_trace_id=trace_id))
    registry.register(DiffFileTool(workspace_root=workspace))
    # MVP-14.2 — evidence layer: turn web pointers into verifiable sources.
    registry.register(WebFetchTool())
    registry.register(RssFetchTool())
    # Academic paper search — Semantic Scholar Graph API (no key needed).
    registry.register(SemanticScholarSearchTool())

    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=True)

    policy = PolicyGate(registry)
    # Optional safety brake: when AGENT_REQUIRE_WRITE_APPROVAL is truthy, the
    # agent/runtime loop must get human approval before creating even a new
    # (reversible) file. Overwrites (irreversible) and external actions already
    # escalate; this closes the new-file path. Off by default so existing
    # behaviour and the test suite are unchanged. Operator scripts that write
    # files directly never pass through this gate and are therefore unaffected.
    if _env_bool("AGENT_REQUIRE_WRITE_APPROVAL", False):
        policy.escalate_reversible_tools = frozenset({"file_write"})
    # Optional safety brake: when AGENT_FREEZE_AUTO_MEMORY is truthy, the
    # agent may not silently grow its own persistent memory. The knowledge
    # pipeline writes 'agent-auto' records straight through the memory write
    # policy — a side channel PolicyGate never sees (the file_write approval
    # gate does not cover memory). Freezing that source forces every
    # agent-initiated memory write to be rejected; user ':remember' writes
    # (source='user-explicit') are unaffected. Off by default.
    if _env_bool("AGENT_FREEZE_AUTO_MEMORY", False):
        write_policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
    else:
        write_policy = MemoryWritePolicy()
    budget_ledger = BudgetLedger.from_env(
        path=workspace / DEFAULT_BUDGET_LEDGER_PATH,
        logger=logger,
        config_path=workspace / "config" / "budget_limits.json",
    )
    model_usage_ledger = ModelUsageLedger.from_env(
        path=workspace / DEFAULT_MODEL_USAGE_PATH,
        logger=logger,
        budget_ledger=budget_ledger,
    )
    # Block 7 (audit W3, 2026-09-03): every egress tool charges `web_fetches`
    # on the same persistent ledger the model calls use. Before this the
    # counter had limits, a kill-switch and a health line, and no charger.
    for egress_tool in ("web_search", "web_fetch", "rss_fetch"):
        registry.get(egress_tool).budget_ledger = budget_ledger
    model_router = ModelRouter.from_env(usage_ledger=model_usage_ledger)
    # The eye on his own models (operator's word 2026-09-04, «ставь глаз»):
    # read-only, registered for every path including the unattended one.
    from tools.model_roster import ModelRosterTool

    registry.register(ModelRosterTool(
        usage_ledger=model_usage_ledger, budget_ledger=budget_ledger,
    ))

    # Agent-as-tool: spawn_subagent must be registered AFTER policy and
    # model_router are built, because it holds references to both.
    # It is intentionally absent from _SAFE_SUBAGENT_TOOLS so sub-agents
    # cannot recursively spawn further sub-agents.
    from tools.spawn_subagent import SpawnSubagentTool

    registry.register(SpawnSubagentTool(
        workspace_root=workspace,
        policy=policy,
        model_router=model_router,
        parent_registry=registry,
        log_dir=workspace / "logs",
    ))

    llm = model_router.for_role(ModelRole.SYNTHESIZER)
    planner = LLMPlanner(
        llm=model_router.for_role(ModelRole.PLANNER),
        registry=registry, workspace=str(workspace),  # адрес уроков: см. kwarg
    )
    memory = WorkingMemory() if with_memory else None

    # Уточнение ждёт ответа на диске: спрашивает один процесс, отвечает другой.
    # Условие — рабочая память, см. `core/pending_clarification.py`.
    pending_clarification_path = (
        workspace / DEFAULT_PENDING_CLARIFICATION_PATH if with_memory else None
    )

    persistent_store: PersistentMemoryStore | None = None
    source_registry_store: SourceRegistryStore | None = None
    user_profile_store: UserProfileStore | None = None
    assumption_store: AssumptionStore | None = None  # Layer 5
    episodic_store: EpisodicMemoryStore | None = None
    procedural_store: ProceduralMemoryStore | None = None
    consolidation_store: MemoryConsolidationStore | None = None
    causal_store = None
    if with_persistent:
        full_path = persistent_path or (workspace / DEFAULT_PERSISTENT_PATH)
        persistent_store = PersistentMemoryStore(full_path)
        source_registry_store = SourceRegistryStore(
            workspace / DEFAULT_SOURCE_REGISTRY_PATH
        )
        user_profile_store = UserProfileStore(workspace / DEFAULT_USER_PROFILE_PATH)
        assumption_store = AssumptionStore(workspace / DEFAULT_ASSUMPTIONS_PATH)  # Layer 5
        # Register the memory bank tool to persist and retrieve agent memories.
        registry.register(MemoryBankTool(store=persistent_store, policy=write_policy))
        # The read half of the same door (authority change 2026-09-04): a
        # bounded, low-trust search of his own durable memory, by his hand.
        registry.register(MemoryRecallTool(store=persistent_store))

    # Experience memory is a SEPARATE axis, deliberately outside the
    # `with_persistent` block above. It used to be nested under both
    # `with_persistent` and `with_memory`, which tied episodic/procedural
    # learning to working memory and left the unattended agent unable to
    # record or recall experience at all.
    if with_experience:
        episodic_store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
        procedural_store = ProceduralMemoryStore(
            workspace / DEFAULT_PROCEDURAL_MEMORY_PATH
        )
        # consolidation_store deliberately NOT constructed since 2026-08-22
        # (MIR-044, operator ruling 2026-07-19): the tally is computed on
        # demand by `:memory-consolidate` / `:smart-memory`; nothing persists.
        from core.causal_store import CausalObservationStore
        causal_store = CausalObservationStore(workspace / DEFAULT_CAUSAL_PATH)

    logger.log(
        "session_start",
        {
            "trace_id": trace_id,
            "workspace": str(workspace),
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "model_routes": model_router.routing_summary(),
            "model_registry": model_router.registry_summary(),
            "tools": [t.name for t in registry.list()],
            "memory": memory.session_id if memory else None,
            "persistent_path": str(persistent_store.path) if persistent_store else None,
            "source_registry_path": (
                str(source_registry_store.path) if source_registry_store else None
            ),
            "episodic_memory_path": (
                str(episodic_store.path) if episodic_store else None
            ),
            "procedural_memory_path": (
                str(procedural_store.path) if procedural_store else None
            ),
            "memory_consolidation_path": (
                str(consolidation_store.path) if consolidation_store else None
            ),
            "model_usage_path": str(workspace / DEFAULT_MODEL_USAGE_PATH),
            "budget_ledger_path": str(workspace / DEFAULT_BUDGET_LEDGER_PATH),
            "knowledge_auto_write": _env_bool("AGENT_KNOWLEDGE_AUTO_WRITE", True),
            "approval": type(approval_provider).__name__ if approval_provider else None,
        },
    )

    # Emit persistent_memory_load up front so traces always show what the
    # session started with — even if the store is empty.
    if persistent_store is not None:
        records = persistent_store.load()
        logger.log(
            "persistent_memory_load",
            {
                "path": str(persistent_store.path),
                "records_loaded": len(records),
                "ids": [r.id for r in records],
            },
        )
    if source_registry_store is not None:
        counts = source_registry_store.count()
        logger.log(
            "source_registry_load",
            {
                "path": str(source_registry_store.path),
                "sources_loaded": counts["sources"],
                "claims_loaded": counts["claims"],
            },
        )
    if episodic_store is not None or procedural_store is not None or consolidation_store is not None:
        logger.log(
            "smart_memory_load",
            {
                "episodic_path": str(episodic_store.path) if episodic_store else None,
                "episodic_records": episodic_store.count() if episodic_store else 0,
                "procedural_path": str(procedural_store.path) if procedural_store else None,
                "procedures": procedural_store.count() if procedural_store else 0,
                "consolidation_path": (
                    str(consolidation_store.path) if consolidation_store else None
                ),
                "consolidation_reports": (
                    consolidation_store.count() if consolidation_store else 0
                ),
            },
        )

    return AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        model_router=model_router,
        memory=memory,
        persistent_store=persistent_store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=write_policy,
        memory_write_registry=(
            MemoryWriteRegistry(workspace / "data" / "memory_writes.jsonl")
            if persistent_store is not None
            else None
        ),
        source_registry_store=source_registry_store,
        episodic_store=episodic_store,
        procedural_store=procedural_store,
        consolidation_store=consolidation_store,
        causal_store=causal_store,
        knowledge_auto_write=_env_bool("AGENT_KNOWLEDGE_AUTO_WRITE", True),
        approval_provider=approval_provider,
        user_profile_store=user_profile_store,
        assumption_store=assumption_store,  # Layer 5
        pending_clarification_path=pending_clarification_path,
        experience_retrieval=experience_retrieval,
        episodic_replay=episodic_replay,
        durable_writes=durable_writes,
    )
