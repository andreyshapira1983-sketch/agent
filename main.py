"""CLI entry point for the agent MVP-5.

Interactive sessions have Working Memory (session-scoped turns) AND
Persistent Memory (long-term records on disk, gated by a Write Policy).
The planner + synthesizer see both prior turns and any retrieved long-term
records that share keywords with the current question.

Usage examples:
    # One-shot — no memory, fresh session
    python main.py --ask "How does Dijkstra's algorithm work?"

    # Interactive — multi-turn dialogue with both memories
    python main.py
    > Что такое DuckDuckGo?
    > А кто его основатель?            # follow-up; planner reuses turn 1
    > :remember preference,fact I prefer concise answers in Russian
    > :ingest-source "архитектура автономного Агента.txt"
    > :ingest-project . --limit 40 --dry-run
    > :source-library books
    > :ingest-web "autonomous agent" --sources wikis,science --limit 3 --dry-run
    > :ingest-rss https://www.python.org/blogs/rss/ --limit 5 --dry-run
    > :connectors
    > :connector-plan "monitor Python releases"
    > :memory                             # inspect working + persistent memory
    > :forget mem_abc123                  # delete one persistent record
    > :forget                             # delete ALL persistent records
    > :clear                              # wipe working memory only
    > :quit

    # Interactive with a file hint
    python main.py --file "архитектура автономного Агента.txt"
    > Сколько доменов в файле?            # file_read runs
    > А что в 12.4?                       # planner can reuse cached file artifact
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def _force_utf8_io() -> None:
    """Make REPL safe for non-ASCII input on Windows.

    Python on Windows opens stdin/stdout/stderr in the active console
    code page (cp1251 / cp866 / cp65001 depending on locale + chcp).
    Anything the user types that isn't ASCII therefore round-trips as
    mojibake — and gets persisted to `memory.jsonl` that way too.

    We force UTF-8 explicitly across the three streams. `reconfigure`
    exists on `TextIOWrapper` (Python 3.7+). We also export
    `PYTHONIOENCODING=utf-8` so any subprocess (e.g. shell_exec) inherits
    the same encoding.
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        # `reconfigure` is missing on some embedded interpreters and on
        # already-replaced streams (e.g. when pytest captures them).
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Best-effort: if a stream refuses (e.g. piped from a process
            # that already mangled bytes upstream) we keep going rather
            # than crash the REPL. The audit log will still capture the
            # raw bytes for forensics.
            pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider
from core.approval_inbox import ApprovalInbox
from core.architecture_audit import audit_architecture
from core.budget_ledger import BudgetLedger
from core.capability_request import propose_capability_request
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.work_session import WorkSessionConfig, run_work_session
from core.campaign import (
    CampaignConfig,
    CampaignLedger,
    load_ledger_rows,
    run_campaign,
    summarise_ledger,
)
from core.subagent_memory_scope import (
    needs_delegation,
    propose_subagent,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, format_human_response, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.memory_echo_antibody import MemoryWriteRegistry
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger
from core.model_router import ModelRole, ModelRouter
from core.operator_intent import OperatorIntent, route_operator_intent
from core.strategy_router import classify_operator_strategy
from core.persistent_memory import PersistentMemoryStore
from core.user_profile import UserProfileStore
from core.assumption_registry import AssumptionStore  # Layer 5
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.scheduler import SchedulerStore
from core.source_registry_store import SourceRegistryStore
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
)
from core.task_queue import TaskQueueStore
from tools.base import ToolRegistry
from tools.current_time import CurrentTimeTool
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.list_dir import ListDirTool
from tools.read_logs import ReadLogsTool
from tools.rss_fetch import RssFetchTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.semantic_scholar_search import SemanticScholarSearchTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool

# Parsers and small text helpers live in cli/parsers.py; re-exported here so
# existing imports (`from main import _parse_remember`, …) keep working.
from cli.parsers import (
    _compact_one_line,
    _env_bool,
    _parse_ingest_options,
    _parse_remember,
    _parse_repair_generation_args,
    _parse_source_planning_args,
    _resolve_workspace_text_file,
    _split_meta_args,
    _truncate_text,
)
# Budget / autonomy-readiness commands live in cli/commands_budget.py; the two
# hybrid handlers that also need the operator digest stay in main.py.
from cli.commands_budget import (
    _autonomy_readiness_payload,
    _budget_enforcement_status,
    _budget_ledger_snapshot,
    _format_autonomy_readiness,
    _format_operator_budget_digest,
    _handle_budget_config,
    _handle_budget_status,
    _handle_budget_window_status,
    _next_action_prerequisites,
    _persistent_budget_limits_configured,
)
# Approval-inbox / alert-ack / best-next-action commands live in
# cli/commands_approval.py (no main back-references, so no import cycle).
from cli.commands_approval import (
    _approval_inbox_for,
    _handle_alert_ack,
    _handle_alert_ack_clear,
    _handle_alert_ack_list,
    _handle_approval_abort,
    _handle_approval_decision,
    _handle_approval_list,
    _handle_approval_run,
    _handle_approval_triage,
    _handle_best_next_action,
)
# Memory / hygiene / rollback commands live in cli/commands_memory.py
# (agent-method driven, no main back-references, so no cycle).
from cli.commands_memory import (
    _handle_hygiene,
    _handle_memory_consolidate,
    _handle_rollback,
    _handle_smart_memory,
    _print_persistent,
)
# Model routing / registry / catalog / usage commands live in
# cli/commands_models.py (model_router-driven, no main back-references).
from cli.commands_models import (
    _handle_model_discovery_audit,
    _handle_model_registry_audit,
    _handle_model_usage,
    _handle_models,
    _handle_provider_catalog_refresh,
    _handle_refresh_models,
)
# Misc operator commands (audit / learn / team / connectors) live in
# cli/commands_misc.py (no main back-references, so no cycle).
from cli.commands_misc import (
    _handle_architecture_audit,
    _handle_conflicts,
    _handle_connector_plan,
    _handle_connectors,
    _handle_learn,
    _handle_release_audit,
    _handle_state_store_drill,
    _handle_supply_chain_audit,
    _handle_team_plan,
    _handle_team_run,
)
# Source ingestion / source-registry / planning commands live in
# cli/commands_ingest.py (no main back-references, so no cycle).
from cli.commands_ingest import (
    _handle_implementation_plan,
    _handle_ingest_project,
    _handle_ingest_rss,
    _handle_ingest_source,
    _handle_ingest_web,
    _handle_patch_proposal_plan,
    _handle_self_build_propose,
    _handle_source_library,
    _handle_source_registry,
    _handle_source_review_plan,
)
# Self-repair commands live in cli/commands_repair.py (no main back-references).
from cli.commands_repair import _handle_propose_repair, _handle_repair


DEFAULT_PERSISTENT_PATH = Path("data") / "persistent_memory.jsonl"
DEFAULT_SOURCE_REGISTRY_PATH = Path("data") / "source_registry.jsonl"
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"
DEFAULT_RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
DEFAULT_MODEL_USAGE_PATH = Path("data") / "model_usage.jsonl"
DEFAULT_BUDGET_LEDGER_PATH = Path("data") / "budget_ledger.jsonl"
DEFAULT_EPISODIC_MEMORY_PATH = Path("data") / "episodic_memory.jsonl"
DEFAULT_PROCEDURAL_MEMORY_PATH = Path("data") / "procedural_memory.jsonl"
DEFAULT_MEMORY_CONSOLIDATION_PATH = Path("data") / "memory_consolidation.jsonl"
DEFAULT_USER_PROFILE_PATH = Path("data") / "user_profile.jsonl"
DEFAULT_ASSUMPTIONS_PATH = Path("data") / "assumptions.jsonl"  # Layer 5


def build_agent(
    workspace: Path,
    with_memory: bool,
    with_persistent: bool = True,
    persistent_path: Path | None = None,
    approval_provider: ApprovalProvider | None = None,
) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(ListDirTool(workspace_root=workspace))
    registry.register(WebSearchTool())
    registry.register(FileWriteTool(workspace_root=workspace))
    registry.register(ShellExecTool(workspace_root=workspace))
    # Pure clock primitive — keeps the agent from guessing today's date.
    registry.register(CurrentTimeTool())
    # MVP-13.1 — self-repair diagnostic primitives.
    registry.register(RunTestsTool(workspace_root=workspace))
    registry.register(ReadLogsTool(workspace_root=workspace))
    registry.register(DiffFileTool(workspace_root=workspace))
    # MVP-14.2 — evidence layer: turn web pointers into verifiable sources.
    registry.register(WebFetchTool())
    registry.register(RssFetchTool())
    # Academic paper search — Semantic Scholar Graph API (no key needed).
    registry.register(SemanticScholarSearchTool())

    trace_id = new_trace_id()
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
    model_router = ModelRouter.from_env(usage_ledger=model_usage_ledger)

    # Agent-as-tool: spawn_subagent must be registered AFTER policy and
    # model_router are built, because it holds references to both.
    # It is intentionally absent from _SAFE_SUBAGENT_TOOLS so sub-agents
    # cannot recursively spawn further sub-agents.
    from tools.spawn_subagent import SpawnSubagentTool  # noqa: PLC0415 (local import)
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
        registry=registry,
    )
    memory = WorkingMemory() if with_memory else None

    persistent_store: PersistentMemoryStore | None = None
    source_registry_store: SourceRegistryStore | None = None
    user_profile_store: UserProfileStore | None = None
    assumption_store: AssumptionStore | None = None  # Layer 5
    episodic_store: EpisodicMemoryStore | None = None
    procedural_store: ProceduralMemoryStore | None = None
    consolidation_store: MemoryConsolidationStore | None = None
    if with_persistent:
        full_path = persistent_path or (workspace / DEFAULT_PERSISTENT_PATH)
        persistent_store = PersistentMemoryStore(full_path)
        source_registry_store = SourceRegistryStore(
            workspace / DEFAULT_SOURCE_REGISTRY_PATH
        )
        user_profile_store = UserProfileStore(workspace / DEFAULT_USER_PROFILE_PATH)
        assumption_store = AssumptionStore(workspace / DEFAULT_ASSUMPTIONS_PATH)  # Layer 5
        if with_memory:
            episodic_store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
            procedural_store = ProceduralMemoryStore(
                workspace / DEFAULT_PROCEDURAL_MEMORY_PATH
            )
            consolidation_store = MemoryConsolidationStore(
                workspace / DEFAULT_MEMORY_CONSOLIDATION_PATH
            )

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
        knowledge_auto_write=_env_bool("AGENT_KNOWLEDGE_AUTO_WRITE", True),
        approval_provider=approval_provider,
        user_profile_store=user_profile_store,
        assumption_store=assumption_store,  # Layer 5
    )


def _run_agent_with_budget_guard(
    agent: AgentLoop,
    *,
    user_question: str,
    file_hint: str | None = None,
    workspace: Path | None = None,
    stream: bool = True,
    deep_escalation=None,
) -> str:
    """Run the agent, optionally streaming synthesis tokens to stdout.

    When *stream* is True (default), synthesis tokens are printed to stdout
    as they arrive so the user sees a progressive response.  The full answer
    is still returned for post-processing (memory writes, formatting, etc.).
    """
    if stream:
        # Print a blank line before streaming starts so the answer is visually
        # separated from the spinner / log output on stderr.
        print("\n", end="", flush=True)
        _streaming_done = []

        def _on_token(text: str) -> None:
            print(text, end="", flush=True)
            _streaming_done.append(text)

        try:
            answer = agent.run(
                user_question=user_question,
                file_hint=file_hint,
                on_token=_on_token,
                deep_escalation=deep_escalation,
            )
        except ModelBudgetExceeded as exc:
            answer = f"Model budget exceeded: {exc}"
            agent.log.log("model_budget_blocked", {"error": str(exc)})
            _persist_resumable_budget_stop(
                agent,
                workspace=workspace,
                user_question=user_question,
                file_hint=file_hint,
                blocked=exc,
            )
        # End the streaming line cleanly; the caller will print the formatted
        # version below (which strips Output Contract headers / citations).
        if _streaming_done:
            print()  # newline after streamed tokens
        return answer
    try:
        return agent.run(user_question=user_question, file_hint=file_hint, deep_escalation=deep_escalation)
    except ModelBudgetExceeded as exc:
        message = f"Model budget exceeded: {exc}"
        agent.log.log("model_budget_blocked", {"error": str(exc)})
        _persist_resumable_budget_stop(
            agent,
            workspace=workspace,
            user_question=user_question,
            file_hint=file_hint,
            blocked=exc,
        )
        return message


def _workspace_from_agent(agent: AgentLoop, workspace: Path | None) -> Path | None:
    if workspace is not None:
        return workspace
    log_dir = getattr(getattr(agent, "log", None), "log_dir", None)
    if log_dir is None:
        return None
    try:
        return Path(log_dir).resolve().parent
    except Exception:
        return None


def _budget_block_payload(
    *,
    agent: AgentLoop,
    user_question: str,
    file_hint: str | None,
    blocked: ModelBudgetExceeded,
) -> dict:
    trace_id = getattr(getattr(agent, "log", None), "trace_id", "")
    return {
        "active_goal": f"Answer the question: {user_question}",
        "goal_id": "",
        "original_user_question": user_question,
        "file_hint": file_hint,
        "current_phase": "budget_guard",
        "planned_steps": [],
        "completed_steps": [],
        "remaining_steps": [],
        "stop_reason": "budget_exhausted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "blocked_model": blocked.to_dict(),
        "trace_id": trace_id,
    }


def _existing_paused_checkpoint(agent: AgentLoop) -> dict | None:
    log = getattr(agent, "log", None)
    trace_id = getattr(log, "trace_id", None)
    log_dir = getattr(log, "log_dir", None)
    if not trace_id or log_dir is None:
        return None
    try:
        from core.checkpoint import CheckpointLoader, PHASE_PAUSED

        ctx = CheckpointLoader(Path(log_dir)).load(trace_id)
        if ctx is not None and ctx.last_phase == PHASE_PAUSED and ctx.paused:
            payload = dict(ctx.paused)
            payload.setdefault("trace_id", trace_id)
            return payload
    except Exception:
        return None
    return None


def _persist_resumable_budget_stop(
    agent: AgentLoop,
    *,
    workspace: Path | None,
    user_question: str,
    file_hint: str | None,
    blocked: ModelBudgetExceeded,
) -> None:
    log = getattr(agent, "log", None)
    trace_id = getattr(log, "trace_id", "")
    payload = _existing_paused_checkpoint(agent) or _budget_block_payload(
        agent=agent,
        user_question=user_question,
        file_hint=file_hint,
        blocked=blocked,
    )
    if not payload.get("trace_id"):
        payload["trace_id"] = trace_id

    if payload.get("current_phase") == "budget_guard":
        try:
            from core.checkpoint import CheckpointWriter

            CheckpointWriter(trace_id=trace_id, log_dir=log.log_dir).save_paused(payload)
            agent.log.log(
                "resumable_checkpoint_paused",
                {
                    "current_phase": payload["current_phase"],
                    "stop_reason": payload["stop_reason"],
                    "planned_steps": 0,
                    "completed_steps": 0,
                    "remaining_steps": 0,
                    "blocked_model": payload["blocked_model"],
                },
            )
        except Exception:
            pass

    resolved_workspace = _workspace_from_agent(agent, workspace)
    if resolved_workspace is None:
        return
    try:
        task = _task_queue_for(agent, resolved_workspace).add_paused_checkpoint(
            goal=str(payload.get("active_goal") or user_question),
            report=payload,
        )
        agent.log.log(
            "resumable_task_paused",
            {
                "task_id": task.id,
                "trace_id": payload.get("trace_id"),
                "stop_reason": payload.get("stop_reason"),
            },
        )
    except Exception:
        pass


_REPLY_ONLY_STOP_RE = re.compile(
    r"\breply\s+only\s+with:\s*(?:\"([^\"]+)\"|'([^']+)'|“([^”]+)”)",
    re.IGNORECASE | re.DOTALL,
)


def _local_operator_reply(text: str, agent: AgentLoop | None = None) -> str | None:
    """Return a local response for explicit stop/ack operator instructions.

    TD-001: some operator-control messages are intentionally local and must not
    enter Planner/Synthesizer. Keep this narrow: only honour a quoted
    "Reply only with:" directive when the same instruction explicitly forbids
    the expensive model path.
    """
    normalized = " ".join((text or "").casefold().split())
    if "reply only with:" not in normalized:
        return None
    llm_stop_markers = (
        "do not call planner",
        "do not call planner or synthesizer",
        "do not use claude",
        "do not make a plan with llm",
        "не вызывай planner",
        "не вызывай synthesizer",
        "не используй claude",
        "не использовать claude",
    )
    if not any(marker in normalized for marker in llm_stop_markers):
        return None
    match = _REPLY_ONLY_STOP_RE.search(text)
    if match is None:
        return None
    answer = next((part for part in match.groups() if part), "").strip()
    if not answer:
        return None
    if agent is not None:
        agent.log.log(
            "local_operator_reply",
            {"reason": "reply_only_stop_instruction", "answer_preview": answer[:120]},
        )
    return answer


def _handle_local_operator_reply(text: str, agent: AgentLoop) -> bool:
    answer = _local_operator_reply(text, agent)
    if answer is None:
        return False
    print("\n" + format_human_response(answer) + "\n")
    return True


def _handle_auto_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    dry_run = True
    include_tests = True
    include_goal = False
    enable_reflection = False
    include_proposals = False
    limit = 5
    learning_limit = 5
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
            i += 1
            continue
        if token == "--tests":
            include_tests = True
            i += 1
            continue
        if token == "--no-tests":
            include_tests = False
            i += 1
            continue
        if token == "--include-goal":
            include_goal = True
            i += 1
            continue
        if token == "--reflect":
            enable_reflection = True
            i += 1
            continue
        if token == "--include-proposals":
            include_proposals = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--learning-limit":
            if i + 1 >= len(tokens):
                print("Usage: --learning-limit requires a number", file=sys.stderr)
                return True
            try:
                learning_limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --learning-limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    goal = " ".join(goal_parts).strip() or "project health"
    # --include-goal only makes sense when a real goal was provided
    if include_goal and goal == "project health":
        print(
            "(auto-run: --include-goal requires a goal text, e.g.: "
            ":auto-run --include-goal проверь покрытие тестов)",
            file=sys.stderr,
        )
        return True

    try:
        report = AutonomousRuntime(
            agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        ).run(
            AutonomousRuntimeConfig(
                goal=goal,
                dry_run=dry_run,
                limit=limit,
                include_tests=include_tests,
                include_goal=include_goal,
                learning_limit=learning_limit,
                enable_reflection=enable_reflection,
                include_proposals=include_proposals,
            )
        )
    except Exception as exc:
        print(f"(auto-run failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_work_session(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :work-session command — bounded multi-cycle session."""
    tokens = _split_meta_args(rest)
    dry_run = False
    minutes: float = 10.0
    max_cycles: int = 3
    report_every: int = 1
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
            i += 1
            continue
        if token == "--minutes":
            if i + 1 >= len(tokens):
                print("Usage: --minutes requires a number", file=sys.stderr)
                return True
            try:
                minutes = float(tokens[i + 1])
            except ValueError:
                print("Usage: --minutes requires a positive number", file=sys.stderr)
                return True
            i += 2
            continue
        if token in ("--max-cycles", "--cycles"):
            if i + 1 >= len(tokens):
                print(f"Usage: {token} requires a number", file=sys.stderr)
                return True
            try:
                max_cycles = int(tokens[i + 1])
            except ValueError:
                print(f"Usage: {token} requires an integer", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--report-every":
            if i + 1 >= len(tokens):
                print("Usage: --report-every requires a number", file=sys.stderr)
                return True
            try:
                report_every = int(tokens[i + 1])
            except ValueError:
                print("Usage: --report-every requires an integer", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    try:
        config = WorkSessionConfig(
            goal=" ".join(goal_parts).strip() or "project health",
            dry_run=dry_run,
            minutes=minutes,
            max_cycles=max_cycles,
            report_every=report_every,
        )
    except ValueError as exc:
        print(f"(work-session config error: {exc})", file=sys.stderr)
        return True

    print(
        f"(work-session goal={config.goal!r} dry_run={config.dry_run} "
        f"minutes={config.minutes} max_cycles={config.max_cycles} "
        f"report_every={config.report_every})",
        file=sys.stderr,
    )

    try:
        result = run_work_session(
            config,
            agent=agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        )
    except Exception as exc:
        print(f"(work-session failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)
    return True


def _handle_campaign_start(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :campaign-start — a budgeted multi-cycle autonomous campaign.

    Unlike :work-session (a fixed time/cycle health loop), a campaign records a
    per-cycle ledger (goal, best-next-action, cost, result, proposal,
    reason_if_idle) and stops honestly: when the budget is spent, or after N
    consecutive idle cycles where there is no high-priority action to take. An
    idle cycle spends NO model call. Dry-run by default — effects still flow
    only through the existing approval gate.
    """
    tokens = _split_meta_args(rest)
    dry_run = True
    max_cycles = 24
    max_llm_calls = 100
    max_cost_units = 0
    max_idle_streak = 3
    max_wall_clock_seconds = 0
    cycle_pause_seconds = 0
    max_unproductive_streak = 3
    goal_parts: list[str] = []

    def _int_arg(flag: str, index: int) -> int | None:
        if index + 1 >= len(tokens):
            print(f"Usage: {flag} requires a number", file=sys.stderr)
            return None
        try:
            return int(tokens[index + 1])
        except ValueError:
            print(f"Usage: {flag} requires an integer", file=sys.stderr)
            return None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
            i += 1
            continue
        if token in ("--cycles", "--max-cycles"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_cycles = value
            i += 2
            continue
        if token == "--max-llm-calls":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_llm_calls = value
            i += 2
            continue
        if token == "--max-cost-units":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_cost_units = value
            i += 2
            continue
        if token == "--max-idle":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_idle_streak = value
            i += 2
            continue
        if token in ("--max-wall-clock-seconds", "--max-seconds"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value
            i += 2
            continue
        if token in ("--max-minutes",):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value * 60
            i += 2
            continue
        if token in ("--max-hours",):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value * 3600
            i += 2
            continue
        if token in ("--cycle-pause-seconds", "--pause-seconds"):
            value = _int_arg(token, i)
            if value is None:
                return True
            cycle_pause_seconds = value
            i += 2
            continue
        if token in ("--max-unproductive-streak", "--max-unproductive"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_unproductive_streak = value
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    try:
        config = CampaignConfig(
            goal=" ".join(goal_parts).strip() or "project health",
            dry_run=dry_run,
            max_cycles=max_cycles,
            max_llm_calls=max_llm_calls,
            max_cost_units=max_cost_units,
            max_idle_streak=max_idle_streak,
            max_wall_clock_seconds=max_wall_clock_seconds,
            cycle_pause_seconds=cycle_pause_seconds,
            max_unproductive_streak=max_unproductive_streak,
        )
    except ValueError as exc:
        print(f"(campaign config error: {exc})", file=sys.stderr)
        return True

    print(
        f"(campaign goal={config.goal!r} dry_run={config.dry_run} "
        f"max_cycles={config.max_cycles} max_llm_calls={config.max_llm_calls} "
        f"max_cost_units={config.max_cost_units} max_idle={config.max_idle_streak} "
        f"max_wall_clock_s={config.max_wall_clock_seconds} "
        f"cycle_pause_s={config.cycle_pause_seconds})",
        file=sys.stderr,
    )

    ledger = CampaignLedger(path=workspace / "data" / "campaign_ledger.jsonl")
    try:
        result = run_campaign(
            config,
            agent=agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
            ledger=ledger,
        )
    except Exception as exc:
        print(f"(campaign failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)
    return True


def _handle_campaign_status(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :campaign-status — read the campaign ledger and print a digest.

    Pure read-only operator window over ``data/campaign_ledger.jsonl``: spends
    no budget, creates no agent, touches no state. Answers "what did it do with
    my keys?" by aggregating every recorded cycle (all-time totals, per-result
    counts, distinct goals) and showing the most recent cycles. ``--recent N``
    controls how many trailing rows to show (0 = all).
    """
    tokens = _split_meta_args(rest)
    recent = 10
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--recent":
            if i + 1 >= len(tokens):
                print("Usage: --recent requires a number", file=sys.stderr)
                return True
            try:
                recent = int(tokens[i + 1])
            except ValueError:
                print("Usage: --recent requires an integer", file=sys.stderr)
                return True
            if recent < 0:
                print("Usage: --recent must be >= 0", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"(campaign-status: ignoring unknown argument {token!r})", file=sys.stderr)
        i += 1

    rows = load_ledger_rows(workspace / "data" / "campaign_ledger.jsonl")
    print(summarise_ledger(rows, recent=recent), file=sys.stderr)
    return True


def _handle_capability_request(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :capability-request — propose missing capability boundaries."""
    tokens = _split_meta_args(rest)
    submit = False
    as_json = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        elif token == "--json":
            as_json = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :capability-request <goal> [--submit] [--json]", file=sys.stderr)
        return True

    try:
        request = propose_capability_request(goal)
    except ValueError as exc:
        print(f"(capability-request failed: {exc})", file=sys.stderr)
        return True

    agent.log.log("capability_request_proposed", request.to_dict())
    if as_json:
        print(json.dumps(request.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(request.user_summary(), file=sys.stderr)

    if submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="capability_request",
            summary=f"Capability request: {request.capability_type} — {goal}",
            risk="external",
            reasons=(request.why_needed, request.human_risk_summary),
            payload=request.to_dict(),
        )
        print(f"(capability-request submitted to approval inbox id={item.id})", file=sys.stderr)

    return True


def _handle_subagent_proposal(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :subagent-proposal command — generate an autonomous subagent proposal."""
    tokens = _split_meta_args(rest)
    submit = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :subagent-proposal <goal> [--submit]", file=sys.stderr)
        return True

    hint = "(needs delegation)" if needs_delegation(goal) else "(may not need delegation)"
    print(f"(subagent-proposal goal={goal!r} {hint})", file=sys.stderr)

    try:
        result = propose_subagent(
            goal,
            llm=agent.model_router.for_task(ModelRole.PLANNER, goal),
            logger=agent.logger if hasattr(agent, "logger") else None,
        )
    except Exception as exc:
        print(f"(subagent-proposal failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)

    if result.ok and result.proposal is not None and submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="launch_subagent",
            summary=f"Subagent proposal: {result.proposal.proposed_role} — {goal}",
            risk="reversible",
            reasons=(f"why: {result.proposal.why_needed}",),
            payload=result.proposal.to_dict(),
        )
        print(f"(subagent-proposal submitted to approval inbox id={item.id})", file=sys.stderr)

    return True


def _handle_auto_status(agent: AgentLoop, workspace: Path) -> bool:
    status = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).status()
    status["task_queue"] = _task_queue_for(agent, workspace).summary()
    status["scheduler"] = _scheduler_for(agent, workspace).summary()
    status["model_usage"] = agent.model_router.usage_snapshot()
    status["persistent_budget_windows"] = _budget_ledger_snapshot(agent)
    print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _operator_digest_payload(agent: AgentLoop, workspace: Path) -> dict:
    audit = audit_architecture(workspace)
    runtime_status = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).status()
    task_queue = _task_queue_for(agent, workspace).summary()
    scheduler = _scheduler_for(agent, workspace).summary()
    model_usage = agent.model_router.usage_snapshot()
    budget_windows = _budget_ledger_snapshot(agent)
    payload = {
        "architecture": audit.to_dict(),
        "runtime": runtime_status,
        "task_queue": task_queue,
        "scheduler": scheduler,
        "model_usage": model_usage,
        "persistent_budget_windows": budget_windows,
        "budget_policy": _budget_enforcement_status(budget_windows),
    }
    payload["recommendations"] = _operator_recommendations(payload)
    return payload


def _operator_recommendations(payload: dict) -> list[str]:
    recommendations: list[str] = []
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    if budget_policy.get("warning"):
        recommendations.append(str(budget_policy["warning"]))
    approvals = payload.get("runtime", {}).get("approval_inbox", {})
    pending = int(approvals.get("pending", 0) or 0)
    if pending:
        recommendations.append(
            f"Review {pending} pending approval item(s) before allowing effects."
        )
    architecture = payload.get("architecture", {})
    gaps = architecture.get("priority_gaps", [])
    if gaps:
        first = gaps[0]
        recommendations.append(
            f"Next architecture gap: {first.get('title')} - {first.get('next_step')}"
        )
    source_registry = payload.get("runtime", {}).get("source_registry", {})
    memory_records = int(payload.get("runtime", {}).get("persistent_memory_records", 0) or 0)
    if int(source_registry.get("claims", 0) or 0) and memory_records == 0:
        recommendations.append(
            "Promote only reviewed source-backed claims into persistent memory; auto-write is still off."
        )
    task_queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    if not task_queue.get("pending_due") and not scheduler.get("due"):
        recommendations.append(
            "No due scheduled work is waiting; run a dry health pass when you want active verification."
        )
    return recommendations


def _format_operator_digest(payload: dict) -> str:
    architecture = payload.get("architecture", {})
    runtime = payload.get("runtime", {})
    source_registry = runtime.get("source_registry", {})
    approvals = runtime.get("approval_inbox", {})
    model_usage = payload.get("model_usage") or {}
    totals = model_usage.get("totals", {})
    session_totals = model_usage.get("session_totals", {})
    task_queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    lines = [
        "=== operator digest ===",
        (
            "architecture: "
            f"ready_for_multi_agent_execution={architecture.get('ready_for_multi_agent_execution')} "
            f"status_counts={architecture.get('status_counts', {})}"
        ),
        (
            "source registry: "
            f"sources={source_registry.get('sources', 0)} "
            f"claims={source_registry.get('claims', 0)}"
        ),
        f"persistent memory: records={runtime.get('persistent_memory_records', 0)}",
        (
            "approvals: "
            f"pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}"
        ),
        (
            "queue/scheduler: "
            f"pending_due={task_queue.get('pending_due', 0)} "
            f"scheduled_due={scheduler.get('due', 0)}"
        ),
        (
            "model usage: "
            f"history_calls={totals.get('calls', 0)} "
            f"history_tokens={totals.get('total_tokens', 0)} "
            f"session_calls={session_totals.get('calls', 0)} "
            f"session_tokens={session_totals.get('total_tokens', 0)}"
        ),
    ]
    gaps = architecture.get("priority_gaps", [])
    if gaps:
        lines.append("attention:")
        for gap in gaps[:3]:
            lines.append(f"  - {gap.get('title')}: {gap.get('summary')}")
            lines.append(f"    next: {gap.get('next_step')}")
    else:
        lines.append("attention: none")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("recommended actions:")
        for item in recommendations[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _handle_operator_check(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :operator-check [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    agent.log.log("operator_digest", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_operator_digest(payload), file=sys.stderr)
    return True


def _handle_operator_budget(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :operator-budget [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    budget_payload = {
        "model_usage": payload.get("model_usage"),
        "persistent_budget_windows": payload.get("persistent_budget_windows"),
        "budget_policy": payload.get("budget_policy"),
        "recommendations": [
            item for item in payload.get("recommendations", [])
            if any(
                term in item.casefold()
                for term in ("budget limits", "hour/day", "model", "token", "cost", "spend")
            )
        ],
    }
    agent.log.log("operator_budget_digest", budget_payload)
    if as_json:
        print(json.dumps(budget_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_operator_budget_digest(budget_payload), file=sys.stderr)
    return True


def _handle_urgent_status(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :urgent-status [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    urgent_payload = {
        "approval_inbox": payload.get("runtime", {}).get("approval_inbox", {}),
        "task_queue": payload.get("task_queue", {}),
        "scheduler": payload.get("scheduler", {}),
        "runtime_recommendations": payload.get("recommendations", []),
    }
    agent.log.log("operator_urgent_status", urgent_payload)
    if as_json:
        print(json.dumps(urgent_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_urgent_status(urgent_payload), file=sys.stderr)
    return True


def _format_urgent_status(payload: dict) -> str:
    approvals = payload.get("approval_inbox", {})
    queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    urgent_items: list[str] = []
    pending = int(approvals.get("pending", 0) or 0)
    due_tasks = int(queue.get("pending_due", 0) or 0)
    due_schedules = int(scheduler.get("due", 0) or 0)
    if pending:
        urgent_items.append(f"{pending} approval item(s) are waiting.")
    if due_tasks:
        urgent_items.append(f"{due_tasks} queued task(s) are due.")
    if due_schedules:
        urgent_items.append(f"{due_schedules} schedule item(s) are due.")
    lines = [
        "=== urgent status ===",
        f"approvals_pending={pending}",
        f"queue_pending_due={due_tasks}",
        f"scheduler_due={due_schedules}",
    ]
    if urgent_items:
        lines.append("urgent items:")
        lines.extend(f"  - {item}" for item in urgent_items)
    else:
        lines.append("urgent items: none")
    return "\n".join(lines)


def _handle_next_actions(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :next-actions [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    next_payload = {
        "prerequisites": _next_action_prerequisites(payload),
        "priority_gaps": payload.get("architecture", {}).get("priority_gaps", []),
        "recommendations": payload.get("recommendations", []),
    }
    agent.log.log("operator_next_actions", next_payload)
    if as_json:
        print(json.dumps(next_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_next_actions(next_payload), file=sys.stderr)
    return True


def _format_next_actions(payload: dict) -> str:
    lines = ["=== next actions ==="]
    prerequisites = payload.get("prerequisites", [])
    if prerequisites:
        lines.append("prerequisites before long work sessions:")
        for item in prerequisites[:5]:
            lines.append(f"  - {item}")
    gaps = payload.get("priority_gaps", [])
    if gaps:
        lines.append("architecture priorities:")
        for gap in gaps[:3]:
            lines.append(f"  - {gap.get('title')}: {gap.get('next_step')}")
    else:
        lines.append("architecture priorities: none")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("recommended actions:")
        for item in recommendations[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _handle_autonomy_readiness(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :autonomy-readiness [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    readiness_payload = _autonomy_readiness_payload(payload)
    agent.log.log("operator_autonomy_readiness", readiness_payload)
    if as_json:
        print(json.dumps(readiness_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_autonomy_readiness(readiness_payload), file=sys.stderr)
    return True


def _handle_operator_task(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    block = rest.strip()
    if not block:
        print("Usage: :operator-task <task text> or start a block and finish with :end", file=sys.stderr)
        return True
    payload = _operator_task_payload(block, agent, workspace)
    agent.log.log("operator_task_report", payload)
    print(_format_operator_task_report(payload), file=sys.stderr)
    return True


def _operator_task_payload(block: str, agent: AgentLoop, workspace: Path) -> dict:
    text = block.casefold()
    if _looks_like_operator_programming_task(text):
        return _operator_programming_task_payload(block, agent, workspace)

    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    requested_checks = _extract_operator_task_checks(text)
    constraints = _extract_operator_task_constraints(text)
    if not constraints:
        constraints = [
            "no file/code changes",
            "no external side effects",
            "no repair",
            "no allow-effects",
            "no subagents",
            "no automatic persistent memory writes",
        ]
    architecture = digest.get("architecture", {})
    runtime = digest.get("runtime", {})
    queue = digest.get("task_queue", {})
    scheduler = digest.get("scheduler", {})
    model_usage = digest.get("model_usage") or {}
    windows = digest.get("persistent_budget_windows")
    approvals = runtime.get("approval_inbox", {})
    source_registry = runtime.get("source_registry", {})
    working_normally = [
        f"architecture audit loaded with status_counts={architecture.get('status_counts', {})}",
        (
            "source registry available: "
            f"sources={source_registry.get('sources', 0)} claims={source_registry.get('claims', 0)}"
        ),
        (
            "approvals queue visible: "
            f"pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}"
        ),
        (
            "task queue/scheduler visible: "
            f"pending_due={queue.get('pending_due', 0)} scheduler_due={scheduler.get('due', 0)}"
        ),
    ]
    requires_attention = list(digest.get("recommendations", []))
    if not _persistent_budget_limits_configured(windows):
        requires_attention.append(
            "Persistent hour/day budget limits are not configured; long unattended work should wait."
        )
    dangerous_now = list(readiness.get("blockers", []))
    if not dangerous_now:
        dangerous_now.append("No immediate blocker for dry-run operator checks; keep effects disabled.")
    next_step = _operator_task_next_step(requires_attention, dangerous_now)
    return {
        "goal": "safe_system_check",
        "raw_task": block,
        "checks": requested_checks,
        "constraints": constraints,
        "output_contract": [
            "what works normally",
            "what requires attention",
            "what is dangerous to run now",
            "one safest next step",
        ],
        "what_works_normally": working_normally,
        "what_requires_attention": requires_attention,
        "what_is_dangerous_to_run_now": dangerous_now,
        "one_safest_next_step": next_step,
        "evidence": {
            "model_usage": model_usage,
            "persistent_budget_windows": windows,
            "readiness": readiness,
        },
        "prohibited_actions": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
    }


def _looks_like_operator_programming_task(text: str) -> bool:
    if "safe_system_check" in text:
        return False
    has_programming_word = any(
        term in text
        for term in (
            "patch proposal",
            "patch",
            "diff",
            "routing bug",
            "bug",
            "implementation plan",
            "source review",
            "explicit multi-file review",
            "multi-file review",
            "какие файлы менять",
            "какие тесты добавить",
            "план реализации",
            "предложи исправление",
            "предложение исправ",
            "патч",
            "баг",
            "исправ",
        )
    )
    has_file_mention = bool(AgentLoop._extract_path_mentions(text))
    return has_programming_word and has_file_mention


def _operator_programming_task_payload(
    block: str,
    agent: AgentLoop,
    workspace: Path,
) -> dict:
    del workspace
    text = block.casefold()
    goal = _operator_programming_goal(text)
    mode = (
        "explicit_multi_file_review"
        if AgentLoop._is_explicit_multi_file_mode(block)
        else "source_review"
    )
    constraints = _extract_operator_task_constraints(text)
    for item in (
        "read-only planning digest",
        "no file/code changes",
        "no shell execution",
        "no repair",
        "no allow-effects",
        "no subagents",
        "no automatic persistent memory writes",
    ):
        if item not in constraints:
            constraints.append(item)
    file_evidence = _operator_task_file_evidence(block, agent)
    valid_files = file_evidence["files"]
    return {
        "goal": goal,
        "mode": mode,
        "raw_task": block,
        "files": [item["path"] for item in valid_files],
        "rejected_files": file_evidence["rejected"],
        "constraints": constraints,
        "output_contract": [
            "functions/files to inspect/change",
            "tests to add",
            "minimal diff-plan",
            "risks",
            "actions forbidden without approval",
        ],
        "functions_files_to_inspect_change": _operator_patch_file_plan(valid_files),
        "tests_to_add": _operator_patch_tests_to_add(valid_files, text),
        "minimal_diff_plan": _operator_patch_diff_plan(valid_files, text),
        "risks": [
            "Task-block classifier can over-route broad programming language if tests do not pin negative cases.",
            "Patch proposal is evidence-limited to files that passed workspace preflight.",
            "No code should be changed from this report without a separate approval/apply step.",
        ],
        "actions_forbidden_without_approval": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
        "evidence": {
            "files": valid_files,
            "rejected_files": file_evidence["rejected"],
            "validation": "workspace-relative read-only file preflight",
        },
        "prohibited_actions": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
    }


def _operator_programming_goal(text: str) -> str:
    if any(term in text for term in ("patch", "патч", "diff", "исправ", "bug", "баг")):
        return "patch_proposal"
    if any(term in text for term in ("implementation plan", "план реализации")):
        return "implementation_plan"
    return "source_review"


def _operator_task_file_evidence(block: str, agent: AgentLoop) -> dict:
    root = agent._file_read_workspace_root()
    requested_paths = AgentLoop._extract_path_mentions(block)
    if root is None:
        return {
            "files": [],
            "rejected": [
                {"path": path, "reason": "file_read is not registered"}
                for path in requested_paths
            ],
        }
    files: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw_path in requested_paths:
        item = agent._validate_user_file_path(raw_path, workspace=root)
        if item["ok"] is not True:
            rejected.append({"path": raw_path, "reason": str(item["reason"])})
            continue
        target: Path = item["target"]
        rel_path = item["relative_path"].as_posix()
        key = rel_path.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            text = target.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            rejected.append({"path": raw_path, "reason": "file is not valid UTF-8"})
            continue
        files.append({
            "source_id": f"file:{rel_path}",
            "path": rel_path,
            "bytes": len(text.encode("utf-8")),
            "lines": len(text.splitlines()),
            "preview": _compact_one_line(text, limit=120),
        })
    return {"files": files, "rejected": rejected}


def _operator_patch_file_plan(files: list[dict]) -> list[str]:
    if not files:
        return ["No valid workspace files passed preflight; inspect files explicitly before proposing a patch."]
    plan: list[str] = []
    for item in files:
        path = item["path"]
        lowered = path.casefold()
        if lowered.endswith("core/operator_intent.py"):
            plan.append(f"{path} - classify operator task/source-review/patch wording before project_health.")
        elif lowered.endswith("main.py"):
            plan.append(f"{path} - dispatch and format programming operator-task reports read-only.")
        elif "test" in lowered:
            plan.append(f"{path} - add routing and operator-task regression tests.")
        else:
            plan.append(f"{path} - inspect as explicit task evidence before any patch.")
    return plan


def _operator_patch_tests_to_add(files: list[dict], text: str) -> list[str]:
    tests = [
        ":operator-task block containing patch proposal + filenames returns goal=patch_proposal.",
        ":operator-task block containing explicit multi-file review validates mentioned workspace files.",
        "safe system check block still returns goal=safe_system_check.",
    ]
    if any("operator_intent.py" in item["path"] for item in files) or "routing" in text:
        tests.append("routing/source-review wording is not captured by project_health.")
    return tests


def _operator_patch_diff_plan(files: list[dict], text: str) -> list[str]:
    del text
    if not files:
        return ["Stop: collect valid file evidence first; do not generate a patch from missing files."]
    return [
        "Add a small task classifier before the generic safe_system_check path.",
        "Extract and validate only user-mentioned file paths inside the workspace.",
        "Return a read-only patch proposal report with files, tests, risks, and forbidden actions.",
        "Keep file_write/shell_exec/repair out of the operator-task path.",
        "Run targeted operator tests, then the full pytest suite.",
    ]


def _extract_operator_task_checks(text: str) -> list[str]:
    mapping = (
        ("architecture", ("architecture", "архитектур")),
        ("runtime", ("runtime", "автоном", "рантайм")),
        ("budget", ("budget", "бюджет", "лимит", "токен", "cost")),
        ("model usage", ("model usage", "модел", "токен")),
        ("approvals", ("approval", "approvals", "одобр", "разреш")),
        ("source registry", ("source registry", "source-registry", "источник")),
        ("scheduler", ("scheduler", "schedule", "распис")),
        ("task queue", ("task queue", "queue", "очеред")),
        ("tests", ("tests", "тест", "pytest")),
    )
    checks = [name for name, terms in mapping if any(term in text for term in terms)]
    return checks or ["architecture", "runtime", "budget", "approvals", "source registry"]


def _extract_operator_task_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    if any(term in text for term in ("ничего не меняй", "no changes", "do not change")):
        constraints.append("no file/code changes")
    if any(term in text for term in ("ничего не запис", "no writes", "do not write")):
        constraints.append("no writes except normal audit logs")
    if "approval" in text or "одобр" in text:
        constraints.append("require approval before any effect")
    if any(term in text for term in ("без repair", "no repair", "не чини")):
        constraints.append("no repair")
    if any(term in text for term in ("без subagent", "no subagent", "не запускай sub")):
        constraints.append("no subagents")
    if any(term in text for term in ("без allow-effects", "no allow-effects", "не включай эффекты")):
        constraints.append("no allow-effects")
    return constraints


def _operator_task_next_step(requires_attention: list[str], dangerous_now: list[str]) -> str:
    for item in requires_attention + dangerous_now:
        lowered = item.casefold()
        if "budget" in lowered or "hour/day" in lowered or "лимит" in lowered:
            return "Configure persistent budget limits before any long unattended operator session."
    if requires_attention:
        return requires_attention[0]
    return "Run the same operator task again after the next code change to confirm the state stayed stable."


def _format_operator_task_report(payload: dict) -> str:
    if payload.get("goal") != "safe_system_check":
        return _format_operator_programming_task_report(payload)
    lines = [
        "=== operator task report ===",
        f"goal: {payload.get('goal')}",
        "constraints:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    lines.append("checks:")
    lines.extend(f"  - {item}" for item in payload.get("checks", []))
    lines.append("1. what works normally:")
    lines.extend(f"  - {item}" for item in payload.get("what_works_normally", []))
    lines.append("2. what requires attention:")
    attention = payload.get("what_requires_attention", [])
    lines.extend(f"  - {item}" for item in (attention or ["nothing urgent"]))
    lines.append("3. what is dangerous to run now:")
    dangerous = payload.get("what_is_dangerous_to_run_now", [])
    lines.extend(f"  - {item}" for item in (dangerous or ["nothing identified"]))
    lines.append("4. one safest next step:")
    lines.append(f"  - {payload.get('one_safest_next_step')}")
    return "\n".join(lines)


def _format_operator_programming_task_report(payload: dict) -> str:
    lines = [
        "=== operator task report ===",
        f"goal: {payload.get('goal')}",
        f"mode: {payload.get('mode')}",
        "constraints:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    files = payload.get("evidence", {}).get("files", [])
    lines.append("validated files:")
    if files:
        for item in files:
            lines.append(
                f"  - {item.get('source_id')} "
                f"bytes={item.get('bytes')} lines={item.get('lines')}"
            )
    else:
        lines.append("  - none")
    rejected = payload.get("rejected_files", [])
    if rejected:
        lines.append("rejected files:")
        lines.extend(
            f"  - {item.get('path')}: {item.get('reason')}"
            for item in rejected
        )
    lines.append("1. functions/files to inspect/change:")
    lines.extend(f"  - {item}" for item in payload.get("functions_files_to_inspect_change", []))
    lines.append("2. tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("3. minimal diff-plan:")
    lines.extend(f"  {idx}. {item}" for idx, item in enumerate(payload.get("minimal_diff_plan", []), start=1))
    lines.append("4. risks:")
    lines.extend(f"  - {item}" for item in payload.get("risks", []))
    lines.append("5. actions forbidden without approval:")
    lines.extend(f"  - {item}" for item in payload.get("actions_forbidden_without_approval", []))
    return "\n".join(lines)


def _handle_operator_capability_check(agent: AgentLoop, workspace: Path) -> bool:
    payload = _operator_capability_payload(agent, workspace)
    agent.log.log("operator_capability_check", payload)
    print(_format_operator_capability_check(payload), file=sys.stderr)
    return True


def _operator_capability_payload(agent: AgentLoop, workspace: Path) -> dict:
    digest = _operator_digest_payload(agent, workspace)
    architecture = digest.get("architecture", {})
    runtime = digest.get("runtime", {})
    source_registry = runtime.get("source_registry", {})
    approvals = runtime.get("approval_inbox", {})
    queue = digest.get("task_queue", {})
    scheduler = digest.get("scheduler", {})
    return {
        "wired": [
            "local operator digest/status commands",
            f"source registry visible: sources={source_registry.get('sources', 0)} claims={source_registry.get('claims', 0)}",
            f"approval inbox visible: pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}",
            f"task queue/scheduler visible: pending_due={queue.get('pending_due', 0)} scheduler_due={scheduler.get('due', 0)}",
            "model usage and persistent budget windows are inspectable",
        ],
        "dry_run_only": [
            "autonomous runtime health passes should stay dry-run until readiness is green",
            "learning/ingestion can preview sources without memory auto-write",
            "self-repair proposals can be reviewed before any apply step",
        ],
        "requires_approval": [
            "allow-effects autonomous runtime",
            "file writes, repair apply, shell execution, external sends, spending",
            "persistent memory promotion beyond explicit user-approved notes",
        ],
        "not_implemented_or_limited": [
            gap.get("title")
            for gap in architecture.get("priority_gaps", [])
            if gap.get("title")
        ],
        "recommendations": digest.get("recommendations", []),
    }


def _format_operator_capability_check(payload: dict) -> str:
    lines = ["=== operator capabilities ==="]
    sections = (
        ("wired", "wired now"),
        ("dry_run_only", "dry-run / supervised only"),
        ("requires_approval", "requires approval"),
        ("not_implemented_or_limited", "not implemented / limited"),
        ("recommendations", "operator recommendations"),
    )
    for key, title in sections:
        lines.append(f"{title}:")
        items = payload.get(key, [])
        lines.extend(f"  - {item}" for item in (items or ["none"]))
    return "\n".join(lines)


def _handle_programming_readiness(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :coding-readiness [--json]", file=sys.stderr)
        return True
    payload = _programming_readiness_payload(agent, workspace)
    agent.log.log("operator_programming_readiness", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_programming_readiness(payload), file=sys.stderr)
    return True


def _programming_readiness_payload(agent: AgentLoop, workspace: Path) -> dict:
    digest = _operator_digest_payload(agent, workspace)
    tool_names = {tool.name for tool in agent.registry.list()}
    test_files = sorted((workspace / "tests").glob("test_*.py")) if (workspace / "tests").exists() else []
    core_files = {
        "operator_intent": (workspace / "core" / "operator_intent.py").exists(),
        "loop": (workspace / "core" / "loop.py").exists(),
        "repair_proposal": (workspace / "core" / "repair_proposal.py").exists(),
        "governance": (workspace / "core" / "governance.py").exists(),
    }
    can_inspect = "file_read" in tool_names
    can_multi_file_review = bool(core_files["loop"])
    can_propose_patch = bool(core_files["repair_proposal"]) or "diff_file" in tool_names
    can_run_tests = "run_tests" in tool_names or bool(test_files)
    can_explain_rollback = bool(core_files["repair_proposal"]) or "file_write" in tool_names
    status = (
        "ready_for_read_only_programming_planning"
        if can_inspect and can_propose_patch and can_run_tests
        else "limited"
    )
    return {
        "status": status,
        "source_registry": digest.get("runtime", {}).get("source_registry", {}),
        "architecture": {
            "status_counts": digest.get("architecture", {}).get("status_counts", {}),
            "priority_gaps": digest.get("architecture", {}).get("priority_gaps", []),
        },
        "tooling": {
            "registered_tools": sorted(tool_names),
            "file_read": "file_read" in tool_names,
            "diff_file": "diff_file" in tool_names,
            "run_tests": "run_tests" in tool_names,
            "file_write": "file_write" in tool_names,
            "shell_exec": "shell_exec" in tool_names,
        },
        "test_availability": {
            "tests_dir_exists": (workspace / "tests").exists(),
            "pytest_ini_exists": (workspace / "pytest.ini").exists(),
            "test_file_count": len(test_files),
        },
        "capabilities": {
            "can_inspect_files_read_only": can_inspect,
            "can_use_explicit_multi_file_review": can_inspect and can_multi_file_review,
            "can_propose_patch_read_only": can_propose_patch,
            "can_name_targeted_tests": can_run_tests,
            "can_explain_rollback_boundary": can_explain_rollback,
            "can_estimate_risk": True,
        },
        "safe_small_task": (
            "Read explicitly mentioned workspace files, produce a read-only patch "
            "proposal, name targeted tests, and stop before apply/write."
        ),
        "files_to_read_first": [
            "the user-mentioned target file(s)",
            "the nearest existing tests for that behavior",
            "core/operator_intent.py when routing/operator wording is involved",
            "main.py when CLI/meta-command dispatch is involved",
            "core/loop.py when planner/tool/evidence flow is involved",
        ],
        "tests_to_run": [
            "targeted pytest for the touched behavior, e.g. pytest tests/test_operator_intent.py tests/test_cli.py -q",
            "full pytest before commit",
        ],
        "risk_estimation": [
            "read-only inspection and patch proposal are low risk",
            "test execution is medium operational risk because it uses local compute/time",
            "file writes, shell execution, repair apply and rollback touch state and require approval",
        ],
        "requires_approval": [
            "file_write or editing project files",
            "shell_exec beyond explicit user-run hints",
            "repair apply / self-repair controller write phase",
            "rollback that changes files",
            "allow-effects autonomous runtime",
            "persistent memory promotion",
        ],
        "do_not_do_yet": [
            "do not turn this into a generic project health report",
            "do not start Long Work Session Mode from a coding-readiness question",
            "do not use README/docs unless explicitly requested",
            "do not call LLM if local readiness is enough",
        ],
    }


def _format_programming_readiness(payload: dict) -> str:
    lines = [
        "=== programming readiness ===",
        f"status: {payload.get('status')}",
    ]
    registry = payload.get("source_registry", {})
    lines.append(
        "source registry: "
        f"sources={registry.get('sources', 0)} claims={registry.get('claims', 0)}"
    )
    tests = payload.get("test_availability", {})
    lines.append(
        "tests: "
        f"dir={tests.get('tests_dir_exists')} "
        f"pytest_ini={tests.get('pytest_ini_exists')} "
        f"files={tests.get('test_file_count')}"
    )
    lines.append("capabilities:")
    for key, value in (payload.get("capabilities") or {}).items():
        lines.append(f"  - {key}: {value}")
    lines.append(f"safe small task: {payload.get('safe_small_task')}")
    lines.append("files to read first:")
    lines.extend(f"  - {item}" for item in payload.get("files_to_read_first", []))
    lines.append("tests to run:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_run", []))
    lines.append("risk estimation:")
    lines.extend(f"  - {item}" for item in payload.get("risk_estimation", []))
    lines.append("requires approval:")
    lines.extend(f"  - {item}" for item in payload.get("requires_approval", []))
    lines.append("do not do yet:")
    lines.extend(f"  - {item}" for item in payload.get("do_not_do_yet", []))
    return "\n".join(lines)


def _handle_operator_gaps_check(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    payload = {
        "architecture": digest.get("architecture", {}),
        "readiness": readiness,
        "budget_policy": digest.get("budget_policy", {}),
        "next_actions": {
            "prerequisites": _next_action_prerequisites(digest),
            "recommendations": digest.get("recommendations", []),
        },
    }
    agent.log.log("operator_current_gaps_check", payload)
    print(_format_operator_gaps_check(payload), file=sys.stderr)
    return True


def _format_operator_gaps_check(payload: dict) -> str:
    architecture = payload.get("architecture", {})
    readiness = payload.get("readiness", {})
    budget = payload.get("budget_policy", {})
    next_actions = payload.get("next_actions", {})
    lines = [
        "=== current gaps ===",
        (
            "architecture: "
            f"ready_for_multi_agent_execution={architecture.get('ready_for_multi_agent_execution')} "
            f"status_counts={architecture.get('status_counts', {})}"
        ),
        (
            "autonomy readiness: "
            f"state={readiness.get('state')} "
            f"dry_run_runtime_ready={readiness.get('dry_run_runtime_ready')}"
        ),
        (
            "budget: "
            f"tracking={budget.get('tracking_enabled')} "
            f"limits_configured={budget.get('enforcement_enabled')} "
            f"over_limit={budget.get('over_limit')}"
        ),
    ]
    gaps = architecture.get("priority_gaps", [])
    lines.append("priority gaps:")
    if gaps:
        for gap in gaps[:5]:
            lines.append(f"  - {gap.get('title')}: {gap.get('next_step')}")
    else:
        lines.append("  - none")
    blockers = readiness.get("blockers", [])
    lines.append("readiness blockers:")
    lines.extend(f"  - {item}" for item in (blockers or ["none"]))
    prereqs = next_actions.get("prerequisites", [])
    lines.append("prerequisites:")
    lines.extend(f"  - {item}" for item in (prereqs or ["none"]))
    return "\n".join(lines)


def _handle_operator_weakness_finder(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    weaknesses: list[str] = []
    budget = digest.get("budget_policy", {})
    if budget.get("warning"):
        weaknesses.append(str(budget["warning"]))
    weaknesses.extend(readiness.get("blockers", []))
    for gap in digest.get("architecture", {}).get("priority_gaps", [])[:3]:
        title = gap.get("title")
        next_step = gap.get("next_step")
        if title:
            weaknesses.append(f"{title}: {next_step}")
    if not weaknesses:
        weaknesses.append("No live blocker found for local dry-run operator checks.")
    payload = {
        "weaknesses": weaknesses,
        "safe_boundary": [
            "keep allow-effects disabled",
            "prefer local operator status before README/docs synthesis",
            "use dry-run runtime until readiness and budget limits are configured",
        ],
    }
    agent.log.log("operator_weakness_finder", payload)
    print(_format_operator_weakness_finder(payload), file=sys.stderr)
    return True


def _format_operator_weakness_finder(payload: dict) -> str:
    lines = ["=== live weakness digest ===", "weaknesses:"]
    lines.extend(f"  - {item}" for item in payload.get("weaknesses", []))
    lines.append("safe boundary:")
    lines.extend(f"  - {item}" for item in payload.get("safe_boundary", []))
    return "\n".join(lines)


def _handle_next_safe_test(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    payload = {
        "recommended_test": (
            "Run a local no-web operator sanity check: "
            ":operator-check, :operator-budget, :autonomy-readiness, "
            ":next-actions, :source-registry."
        ),
        "why": [
            "It exercises live local state without README synthesis.",
            "It does not require web, shell execution, file writes, repair, or allow-effects.",
            "It confirms budget/readiness/source-registry signals before long work sessions.",
        ],
        "prerequisites": _next_action_prerequisites(digest),
        "avoid": [
            "do not start Long Work Session Mode from this question",
            "do not read README unless the user explicitly asks for documentation",
            "do not run repair/apply or allow-effects",
        ],
    }
    agent.log.log("operator_next_safe_test", payload)
    print(_format_next_safe_test(payload), file=sys.stderr)
    return True


def _format_next_safe_test(payload: dict) -> str:
    lines = [
        "=== next safe test ===",
        f"recommended: {payload.get('recommended_test')}",
        "why:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("why", []))
    prereqs = payload.get("prerequisites", [])
    lines.append("prerequisites to watch:")
    lines.extend(f"  - {item}" for item in (prereqs or ["none"]))
    lines.append("avoid:")
    lines.extend(f"  - {item}" for item in payload.get("avoid", []))
    return "\n".join(lines)


def handle_conversational_operator_input(text: str, agent: AgentLoop, workspace: Path) -> bool:
    strategy = classify_operator_strategy(text)
    agent.log.log(
        "strategy_classified",
        {"strategy": strategy.value, "text_preview": text[:120]},
    )
    intent = route_operator_intent(text)
    if intent is None:
        return False
    agent.log.log("operator_intent", intent.to_dict())
    if intent.kind == "shell_command_hint":
        print(
            "This looks like a shell/PowerShell command. "
            "Run it in PowerShell, not inside the agent REPL.",
            file=sys.stderr,
        )
        return True
    print(
        f"(operator intent: {intent.kind}; internal={intent.command})",
        file=sys.stderr,
    )
    return _dispatch_operator_intent(intent, agent, workspace, original_text=text)


def _dispatch_operator_intent(
    intent: OperatorIntent,
    agent: AgentLoop,
    workspace: Path,
    *,
    original_text: str = "",
) -> bool:
    if intent.kind == "capability_request":
        return _handle_capability_request(original_text, agent, workspace)
    if intent.kind == "safe_self_check":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "capability_check":
        return _handle_operator_capability_check(agent, workspace)
    if intent.kind == "programming_readiness":
        return _handle_programming_readiness("", agent, workspace)
    if intent.kind == "current_gaps_check":
        return _handle_operator_gaps_check(agent, workspace)
    if intent.kind == "weakness_finder":
        return _handle_operator_weakness_finder(agent, workspace)
    if intent.kind == "next_safe_test":
        return _handle_next_safe_test(agent, workspace)
    if intent.kind == "project_health":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "smart_memory_status":
        return _handle_smart_memory("", agent)
    if intent.kind == "model_status":
        return _handle_models("", agent)
    if intent.kind == "budget_status":
        return _handle_operator_budget("", agent, workspace)
    if intent.kind == "approval_status":
        return _handle_approval_list("all", agent, workspace)
    if intent.kind == "urgent_status":
        return _handle_urgent_status("", agent, workspace)
    if intent.kind == "next_actions":
        return _handle_next_actions("", agent, workspace)
    if intent.kind == "autonomy_readiness":
        return _handle_autonomy_readiness("", agent, workspace)
    if intent.kind == "source_review_plan":
        return _handle_source_review_plan(original_text, agent, workspace)
    if intent.kind == "implementation_plan":
        return _handle_implementation_plan(original_text, agent, workspace)
    if intent.kind == "patch_proposal":
        return _handle_patch_proposal_plan(original_text, agent, workspace)
    return False


def _collect_instruction_buffer(
    read_line: Callable[[], str],
) -> tuple[str, bool]:
    """Collect operator instruction lines until a terminator marker.

    Reads lines via ``read_line`` until ``:task-end`` (commit) or
    ``:task-abort`` (discard). Returns ``(text, cancelled)`` where ``text`` is
    the joined+stripped buffer and ``cancelled`` is ``True`` when the operator
    aborted. ``read_line`` may raise ``EOFError``/``KeyboardInterrupt``; that is
    propagated so the caller can treat it as a request to leave the REPL.
    """
    lines: list[str] = []
    while True:
        line = read_line()
        marker = line.strip().lower()
        if marker == ":task-end":
            return "\n".join(lines).strip(), False
        if marker == ":task-abort":
            return "", True
        lines.append(line)


def _print_daemon_inbox_notice(workspace: Path) -> None:
    """Check the approval inbox and print a wake-up notice if items are pending.

    Called once at REPL startup so the user immediately sees anything the
    background daemon (agent_tick.py) found while they were away.
    """
    try:
        from collections import Counter

        inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
        pending = inbox.pending()
        if not pending:
            return

        total = len(pending)
        by_operation = Counter(item.operation for item in pending)

        print(f"\n{'='*60}", file=sys.stderr)
        print(
            f"  DAEMON NOTICE: {total} pending approval item(s)",
            file=sys.stderr,
        )
        print(f"{'='*60}", file=sys.stderr)

        # Always show the breakdown by operation type — compact, one line each.
        for operation, count in by_operation.most_common():
            print(f"  - {operation}: {count}", file=sys.stderr)

        if total <= 5:
            # Small backlog: show each item on a single trimmed line.
            print("", file=sys.stderr)
            for item in pending:
                first_line = item.summary.splitlines()[0] if item.summary else ""
                summary = _truncate_text(first_line, 90)
                print(f"  [{item.id[:16]}] {summary}", file=sys.stderr)

        print(
            "\n  Use :approval-list to review  |  :approval-list all for full detail"
            f"\n{'='*60}\n",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001
        pass   # inbox missing or unreadable — silent; don't block startup


def _preflight_file_hint(file_hint: str | None, workspace: Path) -> tuple[bool, str | None]:
    if not file_hint:
        return True, None
    path = Path(file_hint.strip().replace("\\", "/"))
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if path.exists():
        return True, None
    return (
        False,
        "ERROR: file hint does not exist:\n"
        f"{path}\n\n"
        "No model calls were made.",
    )


def _resume_question_from_checkpoint(ctx) -> str:
    paused = getattr(ctx, "paused", None) or {}
    if not paused:
        return ctx.question
    original = str(paused.get("original_user_question") or ctx.question)
    planned = paused.get("planned_steps") or []
    completed = paused.get("completed_steps") or []
    remaining = paused.get("remaining_steps") or []
    blocked = paused.get("blocked_model") or {}
    return "\n".join(
        [
            "Resume the interrupted task from this saved budget checkpoint.",
            f"Original user question: {original}",
            f"Active goal: {paused.get('active_goal') or original}",
            f"Interrupted phase: {paused.get('current_phase') or ctx.last_phase}",
            f"Stop reason: {paused.get('stop_reason') or 'budget_exhausted'}",
            f"Blocked model/counter: {json.dumps(blocked, ensure_ascii=False)}",
            f"Completed steps: {json.dumps(completed, ensure_ascii=False)}",
            f"Remaining steps: {json.dumps(remaining, ensure_ascii=False)}",
            f"Planned steps: {json.dumps(planned, ensure_ascii=False)}",
            "Continue from the remaining steps when they are still relevant. "
            "Do not repeat completed discovery unless it must be refreshed.",
        ]
    )


def _handle_queue_status(agent: AgentLoop, workspace: Path) -> bool:
    summary = _task_queue_for(agent, workspace).summary()
    print("=== runtime task queue ===", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _handle_scheduler_status(agent: AgentLoop, workspace: Path) -> bool:
    summary = _scheduler_for(agent, workspace).summary()
    print("=== runtime scheduler ===", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _task_queue_for(agent: AgentLoop, workspace: Path) -> TaskQueueStore:
    queue = getattr(agent, "runtime_task_queue", None)
    if queue is None:
        queue = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
        setattr(agent, "runtime_task_queue", queue)
    return queue


def _scheduler_for(agent: AgentLoop, workspace: Path) -> SchedulerStore:
    scheduler = getattr(agent, "runtime_scheduler", None)
    if scheduler is None:
        scheduler = SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH)
        setattr(agent, "runtime_scheduler", scheduler)
    return scheduler


def _parse_runtime_task_options(rest: str) -> tuple[dict, str | None]:
    tokens = _split_meta_args(rest)
    dry_run = True
    include_tests = True
    limit = 5
    learning_limit = 5
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
            i += 1
            continue
        if token == "--tests":
            include_tests = True
            i += 1
            continue
        if token == "--no-tests":
            include_tests = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                return {}, "Usage: --limit requires a number"
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --limit requires a number"
            i += 2
            continue
        if token == "--learning-limit":
            if i + 1 >= len(tokens):
                return {}, "Usage: --learning-limit requires a number"
            try:
                learning_limit = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --learning-limit requires a number"
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    if limit < 1 or learning_limit < 1:
        return {}, "Usage: limits must be >= 1"

    return {
        "goal": " ".join(goal_parts).strip() or "project health",
        "dry_run": dry_run,
        "include_tests": include_tests,
        "limit": limit,
        "learning_limit": learning_limit,
    }, None


def _handle_task_add(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    opts, error = _parse_runtime_task_options(rest)
    if error:
        print(error, file=sys.stderr)
        return True
    task = _task_queue_for(agent, workspace).add(**opts)
    agent.log.log("runtime_task_added", task.to_dict())
    print(
        f"(task added: {task.id}; goal={task.goal}; dry_run={task.dry_run}; "
        f"tests={task.include_tests})",
        file=sys.stderr,
    )
    return True


def _handle_task_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "all"
    tasks = _task_queue_for(agent, workspace).list(status=status)
    if not tasks:
        print(f"(no tasks: status={status})", file=sys.stderr)
        return True
    print(f"=== runtime tasks ({len(tasks)}; status={status}) ===", file=sys.stderr)
    for task in tasks:
        report = task.last_report or {}
        resume_hint = ""
        if task.kind == "resume_checkpoint":
            trace_id = report.get("trace_id") or "-"
            stop_reason = report.get("stop_reason") or task.last_error or "-"
            resume_hint = f" stop_reason={stop_reason} resume={trace_id}"
        print(
            f"  {task.id} [{task.status}] priority={task.priority} "
            f"attempts={task.attempts}/{task.max_attempts} run_after={task.run_after} "
            f"goal={task.goal}{resume_hint}",
            file=sys.stderr,
        )
    return True


def _handle_task_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    limit = 1
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"Usage: unknown :task-run option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    report = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).run_task_queue(_task_queue_for(agent, workspace), max_tasks=limit)
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_task_cancel(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    task_id = rest.strip()
    if not task_id:
        print("Usage: :task-cancel <task_id>", file=sys.stderr)
        return True
    try:
        task = _task_queue_for(agent, workspace).cancel(task_id)
    except KeyError as exc:
        print(f"(task cancel failed: {exc})", file=sys.stderr)
        return True
    print(f"(task cancelled: {task.id})", file=sys.stderr)
    return True


def _parse_schedule_add(rest: str) -> tuple[dict, str | None]:
    tokens = _split_meta_args(rest)
    every_minutes: int | None = None
    name: str | None = None
    task_opts = {
        "dry_run": True,
        "include_tests": True,
        "limit": 5,
        "learning_limit": 5,
    }
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--name":
            if i + 1 >= len(tokens):
                return {}, "Usage: --name requires a value"
            name = tokens[i + 1]
            i += 2
            continue
        if token == "--every-minutes":
            if i + 1 >= len(tokens):
                return {}, "Usage: --every-minutes requires a number"
            try:
                every_minutes = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --every-minutes requires a number"
            i += 2
            continue
        if token == "--dry-run":
            task_opts["dry_run"] = True
            i += 1
            continue
        if token == "--allow-effects":
            task_opts["dry_run"] = False
            i += 1
            continue
        if token == "--tests":
            task_opts["include_tests"] = True
            i += 1
            continue
        if token == "--no-tests":
            task_opts["include_tests"] = False
            i += 1
            continue
        if token in {"--limit", "--learning-limit"}:
            if i + 1 >= len(tokens):
                return {}, f"Usage: {token} requires a number"
            try:
                value = int(tokens[i + 1])
            except ValueError:
                return {}, f"Usage: {token} requires a number"
            key = "limit" if token == "--limit" else "learning_limit"
            task_opts[key] = value
            i += 2
            continue
        if every_minutes is None and token.isdigit():
            every_minutes = int(token)
            i += 1
            continue
        goal_parts.append(token)
        i += 1

    if every_minutes is None:
        return {}, "Usage: :schedule-add <minutes> <goal> [--name NAME]"
    if every_minutes < 1:
        return {}, "Usage: minutes must be >= 1"
    if task_opts["limit"] < 1 or task_opts["learning_limit"] < 1:
        return {}, "Usage: limits must be >= 1"
    goal = " ".join(goal_parts).strip() or "project health"
    return {
        "name": name or f"every-{every_minutes}-min",
        "goal": goal,
        "every_minutes": every_minutes,
        **task_opts,
    }, None


def _handle_schedule_add(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    opts, error = _parse_schedule_add(rest)
    if error:
        print(error, file=sys.stderr)
        return True
    schedule = _scheduler_for(agent, workspace).add(**opts)
    agent.log.log("runtime_schedule_added", schedule.to_dict())
    print(
        f"(schedule added: {schedule.id}; every={schedule.every_minutes}m; "
        f"next={schedule.next_run_at}; goal={schedule.goal})",
        file=sys.stderr,
    )
    return True


def _handle_schedule_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "all"
    schedules = _scheduler_for(agent, workspace).list(status=status)
    if not schedules:
        print(f"(no schedules: status={status})", file=sys.stderr)
        return True
    print(f"=== runtime schedules ({len(schedules)}; status={status}) ===", file=sys.stderr)
    for schedule in schedules:
        print(
            f"  {schedule.id} [{schedule.status}] every={schedule.every_minutes}m "
            f"next={schedule.next_run_at} name={schedule.name} goal={schedule.goal}",
            file=sys.stderr,
        )
    return True


def _schedule_disable_message(rest: str, workspace: Path) -> str:
    schedule_id = rest.strip()
    if not schedule_id:
        return "Usage: :schedule-disable <schedule_id>"
    try:
        schedule = SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH).disable(schedule_id)
    except KeyError:
        return "SCHEDULE_NOT_FOUND"
    return f"SCHEDULE_DISABLED {schedule.id}"


def _handle_schedule_disable(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    print(_schedule_disable_message(rest, workspace), file=sys.stderr)
    return True


def _handle_schedule_tick(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    run_after_tick = False
    limit: int | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--run":
            run_after_tick = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"Usage: unknown :schedule-tick option {token}", file=sys.stderr)
        return True
    if limit is not None and limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    tick = _scheduler_for(agent, workspace).tick(
        task_queue=_task_queue_for(agent, workspace),
        limit=limit,
    )
    agent.log.log("runtime_schedule_tick", tick.to_dict())
    print(tick.user_summary(), file=sys.stderr)
    if run_after_tick:
        report = AutonomousRuntime(
            agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        ).run_task_queue(
            _task_queue_for(agent, workspace),
            max_tasks=limit or 1,
            task_ids=tick.task_ids,
        )
        print(report.user_summary(), file=sys.stderr)
    return True


def _handle_assumptions(rest: str, agent: AgentLoop) -> bool:  # Layer 5
    """Show the most-recent assumptions logged by the Assumption Registry."""
    use_json = "--json" in rest
    store = getattr(agent, "assumption_store", None)
    if store is None:
        print("(assumption store not enabled in this session)", file=sys.stderr)
        return True
    try:
        recent = store.load_recent(20)
    except Exception as exc:
        print(f"(assumption store error: {exc})", file=sys.stderr)
        return True
    if not recent:
        print("(no assumptions recorded yet)", file=sys.stderr)
        return True
    if use_json:
        print(json.dumps([a.to_dict() for a in recent], ensure_ascii=False, indent=2))
        return True
    current_run = getattr(getattr(agent, "log", None), "trace_id", None)
    for a in recent:
        run_tag = " [current]" if a.run_id == current_run else f" [run …{a.run_id[-8:]}]"
        verified_tag = " ✓" if a.verified is True else (" ✗" if a.verified is False else "")
        conf = int(a.confidence * 100)
        print(
            f"  [{a.category}] {a.text} ({conf}%){verified_tag}{run_tag}",
            file=sys.stderr,
        )
    return True


def handle_meta_command(cmd: str, agent: AgentLoop, workspace: Path) -> bool:
    """Returns True if the command was handled (so the REPL should skip the LLM)."""
    head, _, rest = cmd.partition(" ")
    head = head.lower()

    if head in {":mem", ":memory"}:
        if agent.memory is None:
            print("(no working memory in this session)", file=sys.stderr)
        else:
            print(json.dumps(agent.memory.summary(), ensure_ascii=False, indent=2))
        _print_persistent(agent)
        return True

    if head in {":smart-memory", ":memory-status"}:
        return _handle_smart_memory(rest.strip(), agent)

    if head == ":memory-consolidate":
        return _handle_memory_consolidate(rest.strip(), agent)

    if head in {":clear", ":reset"}:
        if agent.memory is None:
            print("(no working memory to clear)", file=sys.stderr)
        else:
            agent.memory.clear()
            agent.log.log("memory_clear", {"session_id": agent.memory.session_id})
            print("(working memory cleared — persistent memory untouched)", file=sys.stderr)
        return True

    if head == ":remember":
        tags, content = _parse_remember(rest)
        if not content:
            print(
                "Usage: :remember [tag1,tag2] <text>\n"
                "Examples:\n"
                "  :remember I prefer Python over JavaScript\n"
                "  :remember preference,fact I prefer concise Russian answers",
                file=sys.stderr,
            )
            return True
        decision, record = agent.remember(content=content, tags=tags, source="user-explicit")
        if decision.decision == "save" and record is not None:
            print(f"(saved as {record.id}; reasons: {'; '.join(decision.reasons)})", file=sys.stderr)
        else:
            print(f"(rejected: {'; '.join(decision.reasons)})", file=sys.stderr)
        return True

    if head == ":forget":
        target = rest.strip()
        if not target or target.lower() == "all":
            n = agent.forget(record_id=None)
            print(f"(deleted {n} persistent records)", file=sys.stderr)
        else:
            n = agent.forget(record_id=target)
            if n:
                print(f"(deleted {target})", file=sys.stderr)
            else:
                print(f"(no record with id {target})", file=sys.stderr)
        return True

    if head == ":ingest-source":
        return _handle_ingest_source(rest.strip(), agent, workspace)

    if head == ":ingest-project":
        return _handle_ingest_project(rest.strip(), agent, workspace)

    if head == ":source-library":
        return _handle_source_library(rest.strip())

    if head in {":source-registry", ":source-status"}:
        return _handle_source_registry(rest.strip(), agent, workspace)

    if head == ":source-review-plan":
        return _handle_source_review_plan(rest.strip(), agent, workspace)

    if head == ":implementation-plan":
        return _handle_implementation_plan(rest.strip(), agent, workspace)

    if head in {":patch-proposal-plan", ":patch-plan"}:
        return _handle_patch_proposal_plan(rest.strip(), agent, workspace)

    if head == ":self-build-propose":
        return _handle_self_build_propose(rest.strip(), agent, workspace)

    if head == ":ingest-web":
        return _handle_ingest_web(rest.strip(), agent, workspace)

    if head == ":ingest-rss":
        return _handle_ingest_rss(rest.strip(), agent, workspace)

    if head == ":connectors":
        return _handle_connectors(rest.strip())

    if head == ":connector-plan":
        return _handle_connector_plan(rest.strip())

    if head in {":models", ":model-routes"}:
        return _handle_models(rest.strip(), agent)

    if head in {":model-registry-audit", ":model-audit"}:
        return _handle_model_registry_audit(rest.strip(), agent)

    if head in {":refresh-models", ":model-catalog-refresh", ":model-refresh"}:
        return _handle_refresh_models(rest.strip(), agent)

    if head in {":model-discovery-audit", ":discovery-audit"}:
        return _handle_model_discovery_audit(rest.strip(), agent)

    if head in {":provider-catalog-refresh"}:
        return _handle_provider_catalog_refresh(rest.strip(), agent)

    if head in {":architecture-audit", ":arch-audit", ":roadmap-audit"}:
        return _handle_architecture_audit(rest.strip(), agent, workspace)

    if head in {":operator-check", ":project-check", ":project-status"}:
        return _handle_operator_check(rest.strip(), agent, workspace)

    if head in {":operator-budget", ":budget-digest"}:
        return _handle_operator_budget(rest.strip(), agent, workspace)

    if head in {":budget-config", ":budget-limits"}:
        return _handle_budget_config(rest.strip(), agent, workspace)

    if head in {":urgent-status", ":operator-urgent"}:
        return _handle_urgent_status(rest.strip(), agent, workspace)

    if head in {":next-actions", ":operator-next"}:
        return _handle_next_actions(rest.strip(), agent, workspace)

    if head in {":autonomy-readiness", ":operator-readiness"}:
        return _handle_autonomy_readiness(rest.strip(), agent, workspace)

    if head in {":coding-readiness", ":programming-readiness"}:
        return _handle_programming_readiness(rest.strip(), agent, workspace)

    if head == ":operator-task":
        return _handle_operator_task(rest, agent, workspace)

    if head in {":learn", ":learn-project"}:
        return _handle_learn(rest.strip(), agent, workspace)

    if head == ":auto-run":
        return _handle_auto_run(rest.strip(), agent, workspace)

    if head in {":work-session", ":work-sess"}:
        return _handle_work_session(rest.strip(), agent, workspace)

    if head in {":campaign-start", ":campaign"}:
        return _handle_campaign_start(rest.strip(), agent, workspace)

    if head in {":campaign-status", ":campaign-ledger"}:
        return _handle_campaign_status(rest.strip(), agent, workspace)

    if head in {":capability-request", ":capability-proposal"}:
        return _handle_capability_request(rest.strip(), agent, workspace)

    if head == ":auto-status":
        return _handle_auto_status(agent, workspace)

    if head in {":conflicts", ":conflict-status"}:
        return _handle_conflicts(rest.strip(), agent, workspace)

    if head == ":budget-status":
        return _handle_budget_status(agent, workspace)

    if head in {":budget-window-status", ":budget-windows", ":budget-ledger"}:
        return _handle_budget_window_status(rest.strip(), agent)

    if head in {":state-store-drill", ":state-drill", ":state-recovery-drill"}:
        return _handle_state_store_drill(rest.strip(), agent, workspace)

    if head in {":release-audit", ":release-hygiene"}:
        return _handle_release_audit(rest.strip(), agent, workspace)

    if head in {":supply-chain-audit", ":supply-audit", ":ci-audit"}:
        return _handle_supply_chain_audit(rest.strip(), agent, workspace)

    if head in {":model-usage", ":usage-models"}:
        return _handle_model_usage(rest.strip(), agent)

    if head in {":team-plan", ":agent-team", ":subagents"}:
        return _handle_team_plan(rest.strip(), agent)

    if head in {":subagent-proposal", ":propose-subagent"}:
        return _handle_subagent_proposal(rest.strip(), agent, workspace)

    if head in {":team-run", ":team-execute", ":subagents-run"}:
        return _handle_team_run(rest.strip(), agent)

    if head == ":approval-list":
        return _handle_approval_list(rest.strip(), agent, workspace)

    if head in {":approval-triage", ":triage"}:
        return _handle_approval_triage(rest.strip(), agent, workspace)

    if head in {":best-next-action", ":next-action", ":bna"}:
        return _handle_best_next_action(rest.strip(), agent, workspace)

    if head in {":ack", ":acknowledge"}:
        return _handle_alert_ack(rest.strip(), agent, workspace)

    if head in {":ack-list", ":acks"}:
        return _handle_alert_ack_list(rest.strip(), agent, workspace)

    if head in {":ack-clear", ":unack"}:
        return _handle_alert_ack_clear(rest.strip(), agent, workspace)

    # Short aliases for approval commands
    if head == ":inbox":
        return _handle_approval_list("pending", agent, workspace)

    if head == ":approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":approval-deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-run":
        return _handle_approval_run(rest.strip(), agent, workspace)

    if head == ":approval-abort":
        return _handle_approval_abort(rest.strip(), agent, workspace)

    if head == ":queue-status":
        return _handle_queue_status(agent, workspace)

    if head == ":scheduler-status":
        return _handle_scheduler_status(agent, workspace)

    if head == ":task-add":
        return _handle_task_add(rest.strip(), agent, workspace)

    if head == ":task-list":
        return _handle_task_list(rest.strip(), agent, workspace)

    if head == ":task-run":
        return _handle_task_run(rest.strip(), agent, workspace)

    if head == ":task-cancel":
        return _handle_task_cancel(rest.strip(), agent, workspace)

    if head == ":schedule-add":
        return _handle_schedule_add(rest.strip(), agent, workspace)

    if head == ":schedule-list":
        return _handle_schedule_list(rest.strip(), agent, workspace)

    if head == ":schedule-disable":
        return _handle_schedule_disable(rest.strip(), agent, workspace)

    if head == ":schedule-tick":
        return _handle_schedule_tick(rest.strip(), agent, workspace)

    if head == ":hygiene":
        return _handle_hygiene(rest.strip(), agent, workspace)

    if head == ":rollback":
        return _handle_rollback(rest.strip(), agent, workspace)

    if head == ":repair":
        return _handle_repair(rest.strip(), agent, workspace)

    if head == ":propose-repair":
        return _handle_propose_repair(rest.strip(), agent, workspace)

    if head in {":help", "?"}:
        print(
            "Commands:\n"
            "  :mem | :memory                  inspect working + persistent memory\n"
            "  :smart-memory [--json]          inspect episodic/procedural/consolidation memory\n"
            "  :memory-consolidate [--json]    link episodes to reusable procedures now\n"
            "  :clear                          wipe working memory only\n"
            "  :remember [tags] <text>         save to persistent memory (Write Policy gated)\n"
            "  :forget [id|all]                delete persistent record(s)\n"
            "  :ingest-source <path> [flags]   ingest one UTF-8 text/code file into Source Registry\n"
            "  :ingest-project [path] [flags]  ingest project text/code files (default limit 80)\n"
            "  :source-library [group|all]     list curated online source families\n"
            "  :source-registry [flags]        list ingested sources and claim counts\n"
            "      flags: --claims  --limit N  --json\n"
            "  :source-review-plan <goal>      compare requested files/sources against Source Registry\n"
            "      flags: --limit N  --json\n"
            "  :implementation-plan <goal>     local source-backed implementation plan\n"
            "      flags: --limit N  --json\n"
            "  :patch-proposal-plan <goal>     local read-only patch proposal plan\n"
            "      flags: --limit N  --json\n"
            "  :self-build-propose             propose a self-build patch or NO_PATCH\n"
            "  :ingest-web <topic> [flags]     search/fetch curated web library sources\n"
            "      flags: --sources wikis|books|science|docs|all|id,id  --limit N  --per-source N\n"
            "  :ingest-rss <url> [flags]       fetch RSS/Atom feed entries into Source Registry\n"
            "      flags: --limit N  --dry-run  --write-memory  --no-memory\n"
            "  :connectors [status] [--json]   list source connectors and rough costs\n"
            "  :connector-plan <goal> [flags]  recommend source connectors for a task\n"
            "      flags: --limit N  --json\n"
            "  :models [--json]                inspect model routes and registry\n"
            "  :model-registry-audit [--json] inspect selected vs available model candidates\n"
            "  :model-discovery-audit [--json] local (no-network) provider discovery readiness\n"
            "  :provider-catalog-refresh --dry-run [--anthropic] [--openai] [--json]\n"
            "                                 dry-run live model discovery + catalog diff (no write)\n"
            "  :architecture-audit [--json]   inspect layers and multi-agent gaps\n"
            "  :operator-check [--json]       conversational project/status digest\n"
            "  :operator-budget [--json]      concise budget + model usage digest\n"
            "  :budget-config [--json]        inspect budget limit config and env overrides\n"
            "  :urgent-status [--json]        approvals + queue + scheduler urgency digest\n"
            "  :next-actions [--json]         architecture priorities + recommendations\n"
            "  :autonomy-readiness [--json]   whether autonomy is safe to run now\n"
            "  :coding-readiness [--json]     safe programming task readiness report\n"
            "  :operator-task ... :end        one safe multi-line operator task report\n"
            "  :task-begin ... :task-end       buffer a complex instruction (bypasses keyword router; :task-abort discards)\n"
            "  :model-usage [--json]           inspect model calls/tokens/cost units\n"
            "  :team-plan <goal> [--json]      dry-run bounded subagent contracts\n"
            "  :team-run <goal> [--json]       dry-run execution walk over subagent contracts\n"
            "  :capability-request <goal> [--submit] [--json]\n"
            "                                    propose missing connector/capability boundaries\n"
            "  :subagent-proposal <goal> [--submit]  autonomous subagent initiative proposal\n"
            "  :learn [goal] [flags]           plan sources, then ingest selected learning set\n"
            "  :learn-project [goal] [flags]   alias for :learn\n"
            "      flags: --dry-run  --write-memory  --no-memory  --limit N\n"
            "  :auto-run [goal] [flags]        bounded autonomous health pass\n"
            "      flags: --dry-run  --allow-effects  --limit N  --learning-limit N  --no-tests\n"
            "  :work-session [goal] [flags]    bounded multi-cycle session with time budget\n"
            "      flags: --dry-run  --allow-effects  --minutes N  --max-cycles N  --report-every N\n"
            "  :campaign-start [goal] [flags]  budgeted autonomous campaign with per-cycle ledger\n"
            "      flags: --dry-run  --allow-effects  --cycles N  --max-llm-calls N  --max-cost-units N  --max-idle N\n"
            "  :campaign-status [--recent N]   read the campaign ledger digest (no budget spent)\n"
            "  :auto-status                    inspect autonomous runtime inbox/status\n"
            "  :conflicts [--limit N|--json]   inspect source claim conflicts and suggestions\n"
            "  :budget-status                  inspect default autonomous runtime budgets\n"
            "  :budget-window-status [--json] inspect persistent hour/day budget windows\n"
            "  :state-store-drill [--json]    prove JSONL quarantine/recovery on an isolated file\n"
            "  :release-audit [--json]         inspect release artifact hygiene exclusions\n"
            "  :supply-chain-audit [--json]   inspect pinned deps and CI release gates\n"
            "  :approval-list [status|all]     list pending/approved/denied approval items\n"
            "  :approval-triage                read-only triage: clusters/duplicates/stale + advice\n"
            "  :best-next-action [--json]      choose the single most important next action (advisory)\n"
            "  :ack <action> [--ttl H] [why]   acknowledge an advisory alert so it stops dominating BNA\n"
            "  :ack-list                       list active acknowledgements\n"
            "  :ack-clear <action>             restore an acknowledged alert to the top-pick race\n"
            "  :approval-approve <id>          mark an approval inbox item approved\n"
            "  :approval-deny <id>             mark an approval inbox item denied\n"
            "  :approval-run <id>              execute one approved whitelisted operation\n"
            "  :approval-abort <id>            mark an approval inbox item aborted\n"
            "  :inbox                          shortcut: list pending approvals\n"
            "  :approve <id>                   shortcut: :approval-approve\n"
            "  :deny <id>                      shortcut: :approval-deny\n"
            "  :queue-status                   inspect runtime task queue summary\n"
            "  :scheduler-status               inspect scheduler summary\n"
            "  :task-add [goal] [flags]        enqueue persistent autonomous task\n"
            "  :task-list [status|all]         list runtime tasks\n"
            "  :task-run [--limit N]           run due pending runtime task(s)\n"
            "  :task-cancel <task_id>          cancel one queued task\n"
            "  :schedule-add <min> <goal>      create recurring scheduler entry\n"
            "      flags: --name NAME  --no-tests  --limit N  --learning-limit N\n"
            "  :schedule-list [status|all]     list schedules\n"
            "  :schedule-disable <id>          disable a runtime schedule without running it\n"
            "  :schedule-tick [--run]          enqueue due schedule tasks, optionally run them\n"
            "  :hygiene [subcmd] [--dry-run]   memory hygiene; subcmd:\n"
            "      backups    delete old .bak.<ts> files (keep last 3, >14d old)\n"
            "      expire     drop persistent records past their TTL\n"
            "      dedupe     collapse near-duplicate persistent records\n"
            "      summarise <tag>  merge records sharing <tag> via LLM\n"
            "      archive [--threshold=N] [--min-age=N]  move low-value records to archive\n"
            "      (no subcmd)      run expire, then dedupe, then backups\n"
            "  :rollback [plan_id]             apply latest compensation plan (or by id);\n"
            "                                  no arg = LIFO pop; 'list' = show registered plans\n"
            "  :repair <target> <proposal> [tests...] [--pattern PAT]\n"
            "                                  guarded self-repair: diff, approval, write, tests, rollback\n"
            "  :propose-repair <target> [tests...] [--pattern PAT] [--trace TRACE]\n"
            "                                  generate a RepairProposal without writing files\n"
            "  :assumptions [--json]           show the last 20 logged planning assumptions\n"
            "  :quit | :exit                   exit\n"
            "  empty line                      ignored (use :quit or Ctrl+C to exit)",
            file=sys.stderr,
        )
        print(
            "\nConversational shortcuts:\n"
            "  Проверь проект и скажи что требует внимания\n"
            "  Покажи какие модели используются\n"
            "  Сколько потрачено токенов и какой бюджет\n"
            "  Есть ли что-то срочное\n"
            "  Что делать дальше\n"
            "  Можно ли запускать автономность",
            file=sys.stderr,
        )
        return True

    if head in {":quit", ":exit"}:
        raise SystemExit(0)

    if head in {":assumptions", ":assumption-log"}:  # Layer 5
        return _handle_assumptions(rest.strip(), agent)

    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Autonomous agent MVP-4 — LLM picks tools, sessions have working memory."
        )
    )
    parser.add_argument(
        "--ask",
        help="One-shot question (no memory). Omit to enter the interactive REPL.",
    )
    parser.add_argument(
        "--file",
        help=(
            "Optional file hint. The planner MAY call file_read with it. "
            "Without this hint, file_read is never used."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root (default: current directory).",
    )
    parser.add_argument(
        "--auto-approve",
        choices=["off", "approve", "deny"],
        default="off",
        help=(
            "Approval policy for escalated (irreversible / external) actions: "
            "'off' (default) = interactive prompts in the REPL, deny in one-shot; "
            "'approve' = auto-approve everything (use only in tests / scripts); "
            "'deny' = auto-deny everything."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="TRACE_ID",
        default=None,
        help=(
            "Resume a previous run by trace ID. If the run completed synthesis, "
            "the cached answer is printed immediately (no LLM call). "
            "Budget-paused runs resume with saved phase/step context; "
            "crash-partial runs are re-run from scratch."
        ),
    )
    parser.add_argument(
        "--reason",
        default=None,
        help=(
            "Deep/Opus escalation reason (one-shot --ask only). Without it, a "
            "deep request downgrades to the standard model — the agent never "
            "opens Opus for itself. Valid: operator_explicitly_requested_opus, "
            "planner_multi_file_architecture_change."
        ),
    )
    parser.add_argument(
        "--expect",
        default=None,
        help=(
            "Expected deep output (used with --reason). Valid: minimal_patch_plan, "
            "architecture_tradeoff, cross_file_synthesis, final_answer_high_stakes."
        ),
    )
    args = parser.parse_args()

    # Must run BEFORE any non-ASCII input flows through stdin / out.
    _force_utf8_io()
    workspace = Path(args.workspace).resolve()
    ask_head = args.ask.lstrip() if args.ask else ""
    head, _, rest = ask_head.partition(" ")
    if head.lower() == ":self-build-propose":
        _handle_self_build_propose(rest.strip(), None, workspace)  # type: ignore[arg-type]
        return 0
    if head.lower() == ":schedule-disable":
        print(_schedule_disable_message(rest.strip(), workspace), file=sys.stderr)
        return 0

    load_dotenv()

    # §3.5 Resume: if --resume is given, look up the checkpoint file and
    # short-circuit before building the full agent stack when possible.
    if args.resume:
        import re as _re
        # Mirror the allowlist that CheckpointWriter uses: alphanumerics plus
        # hyphens and underscores only.  Reject anything with slashes, dots,
        # or other characters that could produce a path-traversal when the
        # loader constructs its file path (core/checkpoint.py:166).
        _SAFE_TRACE_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')
        if not _SAFE_TRACE_RE.match(args.resume):
            print(
                f"[resume] Invalid trace_id {args.resume!r}: "
                "only letters, digits, hyphens and underscores are allowed.",
                file=sys.stderr,
            )
            return 2
        from core.checkpoint import CheckpointLoader as _CPLoader, PHASE_PAUSED as _PHASE_PAUSED
        _log_dir = workspace / "logs"
        _loader = _CPLoader(_log_dir)
        _ctx = _loader.load(args.resume)
        if _ctx is None:
            print(
                f"[resume] No usable checkpoint found for trace_id={args.resume!r}. "
                "Running fresh.",
                file=sys.stderr,
            )
        elif _ctx.last_phase == _PHASE_PAUSED and _ctx.paused:
            print(
                f"[resume] Resuming budget-paused checkpoint "
                f"trace_id={args.resume!r} phase={_ctx.paused.get('current_phase')!r}.",
                file=sys.stderr,
            )
            if not args.ask:
                args.ask = _resume_question_from_checkpoint(_ctx)
            if not args.file:
                args.file = _ctx.file_hint
        elif _ctx.answer is not None:
            # Full cycle completed previously — replay the cached answer.
            print(
                f"[resume] Replaying cached answer for trace_id={args.resume!r} "
                f"(phase={_ctx.last_phase}, artifacts={list(_ctx.artifacts)})",
                file=sys.stderr,
            )
            print("\n" + format_human_response(_ctx.answer) + "\n")
            return 0
        else:
            # Cycle did not complete — fall through to a normal run so the
            # agent re-tries from scratch (safe default).
            print(
                f"[resume] Checkpoint found but synthesis incomplete "
                f"(last_phase={_ctx.last_phase!r}). Re-running from scratch.",
                file=sys.stderr,
            )
            if not args.ask:
                args.ask = _ctx.question
            if not args.file:
                args.file = _ctx.file_hint

    file_hint_ok, file_hint_error = _preflight_file_hint(args.file, workspace)
    if not file_hint_ok:
        print(file_hint_error, file=sys.stderr)
        return 2

    # Approval provider selection. One-shot can't realistically prompt a
    # human, so it falls back to AutoApprover unless the user opted in via
    # --auto-approve. Interactive uses the live CLI prompt by default.
    if args.ask:
        if args.auto_approve == "approve":
            approval_provider: ApprovalProvider = AutoApprover(default="approve")
        elif args.auto_approve == "deny":
            approval_provider = AutoApprover(default="deny")
        else:
            # 'off' in one-shot = no provider wired = escalated tools blocked.
            approval_provider = None

        # with_persistent=False: one-shot must NOT read or mutate
        # data/persistent_memory.jsonl — the docstring at line 9 promises
        # "no memory, fresh session", so persistent memory must be excluded
        # too, not just working (session) memory.
        agent = build_agent(
            workspace,
            with_memory=False,
            with_persistent=False,
            approval_provider=approval_provider,
        )
        # Explicit ':' meta-commands take precedence over fuzzy intent routing,
        # mirroring the interactive REPL — otherwise e.g. ':campaign-start
        # --max-cost-units 0' is misread as a budget query by the classifier.
        ask_head = args.ask.lstrip()
        if ask_head.startswith(":") or ask_head == "?":
            if handle_meta_command(ask_head, agent, workspace):
                return 0
            print(f"(unknown command: {ask_head})", file=sys.stderr)
            return 0
        if _handle_local_operator_reply(args.ask, agent):
            return 0
        if handle_conversational_operator_input(args.ask, agent, workspace):
            return 0
        # Deep/Opus escalation is opt-in and operator-driven: only an explicit
        # --reason (with --expect) lets planner/synthesizer reach the deep tier.
        # Without it, deep_escalation stays None and every deep request
        # downgrades to the standard model.
        deep_escalation = None
        if args.reason or args.expect:
            from core.deep_escalation import OperatorEscalation
            deep_escalation = OperatorEscalation(
                reason=args.reason,
                expected_output=args.expect,
            )
        # stream=False: the formatted print below is the sole output.
        # With stream=True the raw Output-Contract tokens arrive first, then
        # format_human_response reprints the same content — double output.
        answer = _run_agent_with_budget_guard(
            agent,
            user_question=args.ask,
            file_hint=args.file,
            workspace=workspace,
            stream=False,
            deep_escalation=deep_escalation,
        )
        print("\n" + format_human_response(answer) + "\n")
        return 0

    if args.auto_approve == "approve":
        approval_provider = AutoApprover(default="approve")
    elif args.auto_approve == "deny":
        approval_provider = AutoApprover(default="deny")
    else:
        approval_provider = CLIApprovalProvider()

    # Interactive — single agent with working + persistent memory + approval UX
    agent = build_agent(workspace, with_memory=True, approval_provider=approval_provider)

    # ── Session rate limiter (T8 / §6) ────────────────────────────────────────
    # Prevents runaway or accidental rapid-fire requests from burning budget or
    # triggering external API rate limits.  30 requests per 60 s is generous
    # for human-paced interaction but catches programmatic loops.
    from core.rate_limiter import CLIRateLimiter
    _rate_limiter = CLIRateLimiter(max_requests=30, window_seconds=60.0)

    # ── Daemon wake-up notice ─────────────────────────────────────────────────
    # If the background daemon ran while the user was away and found problems
    # (failed tests, repair proposals), surface them immediately so the user
    # sees them before the first prompt.
    _print_daemon_inbox_notice(workspace)

    print(
        f"Agent ready. file_hint={args.file or '-'}  memory=on  persistent=on  "
        f"approval={type(approval_provider).__name__}. "
        "Commands: :memory  :smart-memory  :memory-consolidate  :learn  :auto-run  :work-session  :capability-request  :subagent-proposal  :operator-check  :operator-budget  :budget-config  :urgent-status  :next-actions  :autonomy-readiness  :coding-readiness  :operator-task  :task-begin  :conflicts  :budget-status  :budget-window-status  :state-store-drill  :release-audit  :supply-chain-audit  :model-usage  :team-plan  :team-run  :architecture-audit  :model-registry-audit  :model-discovery-audit  :provider-catalog-refresh  :approval-list  :approval-triage  :best-next-action  :ack  :ack-list  :ack-clear  :approval-run  :task-add  :schedule-disable  :schedule-tick  :auto-status  :source-library  :source-registry  :source-review-plan  :implementation-plan  :patch-proposal-plan  :self-build-propose  :connectors  :connector-plan  :models  :ingest-web  :ingest-rss  :ingest-source  :ingest-project  :remember  :forget  :propose-repair  :repair  :help  :quit",
        file=sys.stderr,
    )
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            # An empty Enter must NOT exit — otherwise pasting a long
            # multi-line block whose first line is blank (or pressing
            # Enter to clear the prompt) drops the user back into the
            # parent shell, which then tries to interpret the rest of
            # the paste as commands. Use :quit / :exit / Ctrl+C / EOF.
            continue
        # ── Multi-line input modes ────────────────────────────────────────────
        # Mode 1: explicit block  <<<  … >>>
        #   Start a line with <<< to enter block mode; finish with >>>
        #   Useful when pasting text that contains newlines.
        if q == "<<<":
            block_parts: list[str] = []
            print("(multi-line mode: paste text, finish with >>> on its own line)",
                  file=sys.stderr)
            while True:
                try:
                    bline = input("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                stripped = bline.strip()
                if stripped == ">>>":
                    break
                # Tolerate the terminator glued to the end of a paste:
                # "...вакансии.>>>" should also end the block, otherwise
                # users get stuck in `... ` prompt forever after a single
                # Ctrl+V whose buffer ended with ">>>" without a newline.
                if stripped.endswith(">>>"):
                    block_parts.append(bline.rstrip()[:-3].rstrip())
                    break
                block_parts.append(bline)
            q = "\n".join(block_parts).strip()
            if not q:
                continue
        # Mode 2: line continuation with trailing backslash
        #   Each line ending in \ is joined with the next (backslash removed).
        elif q.endswith("\\"):
            continuation_parts: list[str] = [q[:-1]]
            while True:
                try:
                    cline = input("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if cline.endswith("\\"):
                    continuation_parts.append(cline[:-1])
                else:
                    continuation_parts.append(cline)
                    break
            q = " ".join(p.strip() for p in continuation_parts if p.strip())
        # ─────────────────────────────────────────────────────────────────────
        if q == ":operator-task":
            block_lines: list[str] = []
            print("(operator task block started; finish with :end)", file=sys.stderr)
            while True:
                try:
                    line = input("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if line.strip().lower() == ":end":
                    break
                block_lines.append(line)
            _handle_operator_task("\n".join(block_lines), agent, workspace)
            continue
        # ── CLI instruction buffer ────────────────────────────────────────────
        # :task-begin … :task-end lets the operator compose a complex,
        # multi-line instruction that is sent straight to the agent, bypassing
        # the operator keyword router. This is the reliable way to give an
        # instruction whose wording would otherwise be hijacked by a shortcut
        # (e.g. text that merely *mentions* budget / approval / implementation).
        # :task-abort discards the buffer.
        if q == ":task-begin":
            print(
                "(instruction buffer started; finish with :task-end, "
                "discard with :task-abort)",
                file=sys.stderr,
            )
            try:
                buffered, cancelled = _collect_instruction_buffer(
                    lambda: input("... ")
                )
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if cancelled:
                print("(instruction buffer cancelled)", file=sys.stderr)
                continue
            if not buffered:
                print("(instruction buffer empty — nothing sent)", file=sys.stderr)
                continue
            if _handle_local_operator_reply(buffered, agent):
                continue
            rl = _rate_limiter.consume()
            if not rl.allowed:
                print(
                    f"(rate limit: too many requests — "
                    f"retry in {rl.retry_after_seconds:.1f}s, "
                    f"tokens remaining: {rl.tokens_remaining:.2f})",
                    file=sys.stderr,
                )
                continue
            answer = _run_agent_with_budget_guard(
                agent,
                user_question=buffered,
                file_hint=args.file,
                workspace=workspace,
                stream=False,
            )
            print("\n" + format_human_response(answer) + "\n")
            continue
        if q.startswith(":") or q == "?":
            if handle_meta_command(q, agent, workspace):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
            continue
        if _handle_local_operator_reply(q, agent):
            continue
        if handle_conversational_operator_input(q, agent, workspace):
            continue
        # ── Rate-limit check ─────────────────────────────────────────────────
        rl = _rate_limiter.consume()
        if not rl.allowed:
            print(
                f"(rate limit: too many requests — "
                f"retry in {rl.retry_after_seconds:.1f}s, "
                f"tokens remaining: {rl.tokens_remaining:.2f})",
                file=sys.stderr,
            )
            continue
        answer = _run_agent_with_budget_guard(
            agent,
            user_question=q,
            file_hint=args.file,
            workspace=workspace,
            stream=False,
        )
        print("\n" + format_human_response(answer) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
