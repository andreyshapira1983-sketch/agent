"""Agent construction: tool registry, policy, memory stores, model router."""
from __future__ import annotations

import os
from pathlib import Path

from cli.parsers import _env_bool
from core.approval import ApprovalProvider
from core.assumption_registry import AssumptionStore
from core.budget_ledger import BudgetLedger
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_echo_antibody import MemoryWriteRegistry
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_router import ModelRole, ModelRouter
from core.model_routing_policy import DEFAULT_ROUTING_POLICY_PATH, RoutingPolicyStore
from core.model_usage import ModelUsageLedger
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.pressure_gate import pressure_of
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
)
from core.source_registry_store import SourceRegistryStore
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH as _DEFAULT_RUNTIME_TASKS_PATH
from core.user_profile import UserProfileStore
from tools.base import ToolRegistry
from tools.convert_file import ConvertFileTool
from tools.current_time import CurrentTimeTool
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.find_in_files import FindInFilesTool
from tools.journal_append import JournalAppendTool
from tools.lesson_provenance_tool import LessonProvenanceTool
from tools.list_dir import ListDirTool
from tools.memory_bank import MemoryBankTool
from tools.memory_recall import MemoryRecallTool
from tools.model_route import ModelRouteTool
from tools.patch_check import PatchCheckTool
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
# Owned by core.task_queue, so the daemon can read it without importing this module.
DEFAULT_RUNTIME_TASKS_PATH = _DEFAULT_RUNTIME_TASKS_PATH
DEFAULT_RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
DEFAULT_MODEL_USAGE_PATH = Path("data") / "model_usage.jsonl"
DEFAULT_BUDGET_LEDGER_PATH = Path("data") / "budget_ledger.jsonl"
DEFAULT_EPISODIC_MEMORY_PATH = Path("data") / "episodic_memory.jsonl"
DEFAULT_PROCEDURAL_MEMORY_PATH = Path("data") / "procedural_memory.jsonl"
DEFAULT_MEMORY_CONSOLIDATION_PATH = Path("data") / "memory_consolidation.jsonl"
DEFAULT_USER_PROFILE_PATH = Path("data") / "user_profile.jsonl"
DEFAULT_ASSUMPTIONS_PATH = Path("data") / "assumptions.jsonl"
#: Causal observations, kept on disk so they outlive the turn.
DEFAULT_CAUSAL_PATH = Path("data") / "causal_observations.jsonl"
#: The question a clarification asked about, so the reply extends it instead of replacing it;
#: on disk because the agent that asks and the one that hears the answer are different objects.
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

    Args:
        with_persistent: semantic memory plus source registry, user profile, assumptions.
        with_experience: episodic/procedural stores; None means the same as with_memory.
        durable_writes: allowlist of durable sinks for the agent's lifetime;
            None allows all, frozenset() denies all.
    """
    if with_experience is None:
        with_experience = with_memory
    trace_id = new_trace_id()
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(ListDirTool(workspace_root=workspace))
    registry.register(FindInFilesTool(workspace_root=workspace))
    registry.register(WebSearchTool())
    registry.register(FileWriteTool(workspace_root=workspace))
    registry.register(JournalAppendTool(workspace_root=workspace))
    registry.register(ShellExecTool(workspace_root=workspace))
    registry.register(PythonProbeTool(workspace_root=workspace))
    registry.register(LessonProvenanceTool(workspace_root=str(workspace)))
    registry.register(CurrentTimeTool())
    registry.register(RunTestsTool(workspace_root=workspace))
    registry.register(PatchCheckTool(workspace_root=workspace))
    registry.register(ReadLogsTool(workspace_root=workspace, live_trace_id=trace_id))
    registry.register(DiffFileTool(workspace_root=workspace))
    registry.register(ConvertFileTool(workspace_root=workspace))
    registry.register(WebFetchTool())
    registry.register(RssFetchTool())
    registry.register(SemanticScholarSearchTool())

    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=True)

    policy = PolicyGate(registry)
    # Opt-in brake: even a new (reversible) file needs approval; overwrites already escalate.
    if _env_bool("AGENT_REQUIRE_WRITE_APPROVAL", False):
        policy.escalate_reversible_tools = frozenset({"file_write"})
    # Opt-in brake: 'agent-auto' memory writes bypass PolicyGate, so only the write
    # policy can reject them; user ':remember' writes still pass.
    if _env_bool("AGENT_FREEZE_AUTO_MEMORY", False):
        write_policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
    else:
        write_policy = MemoryWritePolicy()
    budget_ledger = BudgetLedger.from_env(path=workspace / DEFAULT_BUDGET_LEDGER_PATH, logger=logger,
                                          config_path=workspace / "config" / "budget_limits.json")
    policy.pressure = pressure_of(budget_ledger)
    model_usage_ledger = ModelUsageLedger.from_env(
        path=workspace / DEFAULT_MODEL_USAGE_PATH,
        logger=logger,
        budget_ledger=budget_ledger,
    )
    # Egress tools charge `web_fetches` on the same persistent ledger as model calls.
    for egress_tool in ("web_search", "web_fetch", "rss_fetch"):
        registry.get(egress_tool).budget_ledger = budget_ledger
    # The operator's routing policy; the router reads it ahead of the env pins.
    routing_policy = RoutingPolicyStore(workspace / DEFAULT_ROUTING_POLICY_PATH)
    model_router = ModelRouter.from_env(
        usage_ledger=model_usage_ledger, routing_policy=routing_policy,
    )
    registry.register(ModelRouteTool(store=routing_policy))
    # Read-only, registered on every path including the unattended one.
    from tools.model_roster import ModelRosterTool

    registry.register(ModelRosterTool(
        usage_ledger=model_usage_ledger, budget_ledger=budget_ledger,
        routing_policy=routing_policy, workspace=workspace,
    ))

    # Registered after policy and model_router, which it holds; kept out of
    # _SAFE_SUBAGENT_TOOLS so sub-agents cannot spawn further sub-agents.
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
        registry=registry, workspace=str(workspace),
    )
    memory = WorkingMemory() if with_memory else None

    pending_clarification_path = (
        workspace / DEFAULT_PENDING_CLARIFICATION_PATH if with_memory else None
    )

    persistent_store: PersistentMemoryStore | None = None
    source_registry_store: SourceRegistryStore | None = None
    user_profile_store: UserProfileStore | None = None
    assumption_store: AssumptionStore | None = None
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
        assumption_store = AssumptionStore(workspace / DEFAULT_ASSUMPTIONS_PATH)
        registry.register(MemoryBankTool(store=persistent_store, policy=write_policy))
        registry.register(MemoryRecallTool(store=persistent_store))

    # A separate axis on purpose: the unattended agent records experience
    # without working or persistent memory.
    if with_experience:
        episodic_store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
        procedural_store = ProceduralMemoryStore(
            workspace / DEFAULT_PROCEDURAL_MEMORY_PATH
        )
        # consolidation_store stays None: `:memory-consolidate` / `:smart-memory` tally on demand.
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

    # Logged even for an empty store, so every trace shows what the session started with.
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
        assumption_store=assumption_store,
        pending_clarification_path=pending_clarification_path,
        experience_retrieval=experience_retrieval,
        episodic_replay=episodic_replay,
        durable_writes=durable_writes,
        # Off by default until measured (core/observation_round.py).
        observe_before_answer=_env_bool("AGENT_OBSERVE_BEFORE_ANSWER", False),
        max_replan_attempts=max(1, int(os.getenv("AGENT_MAX_REPLANS", "3") or 3)),
    )
