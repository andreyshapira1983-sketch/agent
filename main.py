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
import shlex
import sys
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
from core.budget_governor import BudgetGovernor, BudgetLimits
from core.work_session import WorkSessionConfig, run_work_session
from core.subagent_memory_scope import (
    SubagentProposalResult,
    make_default_proposal,
    needs_delegation,
    propose_subagent,
)
from core.conflict_review import ConflictReview
from core.logger import TraceLogger
from core.ingestion import (
    DEFAULT_PROJECT_LIMIT,
    ingest_files,
    ingest_project,
    ingest_rss_feed,
    ingest_source,
    ingest_web_topic,
)
from core.learning_planner import LearningPlanner
from core.loop import AgentLoop, format_human_response, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger
from core.model_router import ModelRole, ModelRouter
from core.model_registry_audit import audit_model_registry
from core.operator_intent import OperatorIntent, route_operator_intent
from core.strategy_router import classify_operator_strategy
from core.persistent_memory import PersistentMemoryStore
from core.user_profile import UserProfileStore
from core.assumption_registry import AssumptionStore  # Layer 5
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.self_repair import RepairProposal
from core.scheduler import SchedulerStore
from core.source_connectors import (
    SourceConnectorRegistry,
    plan_source_connectors,
    source_connector_payload,
)
from core.source_registry_store import SourceRegistryStore
from core.source_library import list_source_library, source_library_payload
from core.state_store_drill import run_state_store_drill
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
)
from core.release_hygiene import build_release_manifest
from core.supply_chain import audit_supply_chain
from core.task_queue import TaskQueueStore
from core.team_executor import TeamBudget, TeamExecutor
from core.team_plan import TeamPlanner
from tools.base import ToolRegistry
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


DEFAULT_PERSISTENT_PATH = Path("data") / "persistent_memory.jsonl"
DEFAULT_SOURCE_REGISTRY_PATH = Path("data") / "source_registry.jsonl"
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"
DEFAULT_RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
DEFAULT_APPROVAL_INBOX_PATH = Path("data") / "approval_inbox.jsonl"
DEFAULT_MODEL_USAGE_PATH = Path("data") / "model_usage.jsonl"
DEFAULT_BUDGET_LEDGER_PATH = Path("data") / "budget_ledger.jsonl"
DEFAULT_EPISODIC_MEMORY_PATH = Path("data") / "episodic_memory.jsonl"
DEFAULT_PROCEDURAL_MEMORY_PATH = Path("data") / "procedural_memory.jsonl"
DEFAULT_MEMORY_CONSOLIDATION_PATH = Path("data") / "memory_consolidation.jsonl"
DEFAULT_USER_PROFILE_PATH = Path("data") / "user_profile.jsonl"
DEFAULT_ASSUMPTIONS_PATH = Path("data") / "assumptions.jsonl"  # Layer 5


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "approve"}


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
        write_policy=MemoryWritePolicy(),
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
    stream: bool = True,
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
            )
        except ModelBudgetExceeded as exc:
            answer = f"Model budget exceeded: {exc}"
            agent.log.log("model_budget_blocked", {"error": str(exc)})
        # End the streaming line cleanly; the caller will print the formatted
        # version below (which strips Output Contract headers / citations).
        if _streaming_done:
            print()  # newline after streamed tokens
        return answer
    try:
        return agent.run(user_question=user_question, file_hint=file_hint)
    except ModelBudgetExceeded as exc:
        message = f"Model budget exceeded: {exc}"
        agent.log.log("model_budget_blocked", {"error": str(exc)})
        return message


def _parse_remember(rest: str) -> tuple[list[str], str]:
    """Parse `:remember [tag,tag] text...` into (tags, content).

    Tags are detected when the first whitespace-separated token contains a
    comma OR matches a known consent tag. Anything else is treated as part
    of the content with the default tag list.

    ASCII-only identifier policy applies to TAGS (they are identifiers).
    `content` may contain any unicode (it's human text).
    """
    rest = rest.strip()
    if not rest:
        return [], ""
    head, _, tail = rest.partition(" ")
    tag_candidates = {
        "preference", "fact", "decision", "insight",
        "user-approved", "project",
    }
    if "," in head or head.lower() in tag_candidates:
        raw_tags = [t.strip().lower() for t in head.split(",") if t.strip()]
        # Silently drop non-ASCII tags so the user gets the obvious
        # fallback (`user-approved`) instead of a stack trace. Tags are
        # programming identifiers in this codebase; Russian / other
        # unicode belongs in the content body.
        tags = [t for t in raw_tags if t.isascii()]
        if not tags:
            tags = ["user-approved"]
        return tags, tail.strip()
    return ["user-approved"], rest


def _handle_rollback(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """`:rollback` — apply / inspect compensation plans."""
    tokens = rest.split()
    if tokens and tokens[0].lower() == "list":
        if not agent.compensation_log:
            print("(no compensation plans registered)", file=sys.stderr)
            return True
        print(f"=== compensation log ({len(agent.compensation_log)} plan(s), oldest first) ===", file=sys.stderr)
        for p in agent.compensation_log:
            preview = ", ".join(
                f"{a.kind}:{a.path or a.backup_path or '-'}" for a in p.actions
            )
            print(f"  {p.id}  tool={p.tool_name}  actions=[{preview}]  — {p.description}", file=sys.stderr)
        return True

    plan_id: str | None = tokens[0] if tokens else None
    report = agent.rollback(plan_id=plan_id, workspace_root=workspace)
    summary = report.summary()
    skipped = summary.get("skipped_reason")
    if skipped:
        print(f"rollback skipped: {skipped}", file=sys.stderr)
        return True
    ok = summary["ok"]
    nop = summary["noop"]
    err = summary["error"]
    print(
        f"rollback {report.plan_id}: ok={ok}, noop={nop}, error={err}",
        file=sys.stderr,
    )
    for o in summary["outcomes"]:
        print(
            f"  [{o['status']}] {o['kind']} {o.get('path') or o.get('backup_path') or ''} "
            f"— {o['detail']}",
            file=sys.stderr,
        )
    return True


def _handle_hygiene(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Dispatch `:hygiene [subcmd] [args...] [--dry-run]`."""
    tokens = rest.split()
    dry_run = False
    if "--dry-run" in tokens:
        dry_run = True
        tokens = [t for t in tokens if t != "--dry-run"]

    sub = (tokens[0].lower() if tokens else "")
    rest_args = tokens[1:]

    if sub in {"", "all"}:
        # Default order: expire (free TTL records) -> dedupe (collapse
        # what's left) -> backups (filesystem). Summarise is omitted
        # from the bulk run because it needs an explicit tag.
        exp = agent.expire_persistent(dry_run=dry_run)
        dup = agent.dedupe_persistent(dry_run=dry_run)
        bak = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"hygiene (dry_run={dry_run}):\n"
            f"  expire   : {len(exp.expired)} record(s) past TTL\n"
            f"  dedupe   : {len(dup.deleted)} near-duplicate(s) collapsed "
            f"({len(dup.groups)} group(s))\n"
            f"  backups  : {len(bak.deleted)} old .bak.<ts> file(s) removed "
            f"(scanned {bak.scanned})",
            file=sys.stderr,
        )
        return True

    if sub == "expire":
        rep = agent.expire_persistent(dry_run=dry_run)
        print(
            f"expire (dry_run={dry_run}): {len(rep.expired)} record(s) past TTL "
            f"out of {rep.scanned} scanned. ids={rep.expired}",
            file=sys.stderr,
        )
        return True

    if sub == "dedupe":
        rep = agent.dedupe_persistent(dry_run=dry_run)
        print(
            f"dedupe (dry_run={dry_run}): {len(rep.deleted)} duplicate(s) collapsed "
            f"into {len(rep.groups)} group(s); threshold={rep.threshold}",
            file=sys.stderr,
        )
        for g in rep.groups:
            print(
                f"  canonical={g.canonical_id} dups={g.duplicate_ids}",
                file=sys.stderr,
            )
        return True

    if sub == "backups":
        rep = agent.cleanup_backups(workspace, dry_run=dry_run)
        print(
            f"backups (dry_run={dry_run}): scanned {rep.scanned} backup(s), "
            f"deleted {len(rep.deleted)}, kept {len(rep.kept)}; "
            f"keep_last={rep.keep_last}, max_age_days={rep.max_age_days}",
            file=sys.stderr,
        )
        return True

    if sub == "summarise":
        if not rest_args:
            print(
                "Usage: :hygiene summarise <tag> [--dry-run]",
                file=sys.stderr,
            )
            return True
        tag = rest_args[0]
        rep = agent.summarise_persistent(tag=tag, dry_run=dry_run)
        if rep.skipped_reason:
            print(
                f"summarise (dry_run={dry_run}): skipped — {rep.skipped_reason}",
                file=sys.stderr,
            )
        else:
            print(
                f"summarise (dry_run={dry_run}): merged "
                f"{len(rep.summarised_ids)} record(s) tagged '{tag}' "
                f"into {rep.new_record_id}",
                file=sys.stderr,
            )
        return True

    if sub == "archive":
        threshold = None
        min_age_days = None
        for arg in rest_args:
            if arg.startswith("--threshold="):
                threshold = float(arg.split("=", 1)[1])
            elif arg.startswith("--min-age="):
                min_age_days = int(arg.split("=", 1)[1])
        rep = agent.archive_persistent(
            threshold=threshold, min_age_days=min_age_days, dry_run=dry_run
        )
        print(
            f"archive (dry_run={dry_run}): scanned {rep.scanned} record(s), "
            f"archived {len(rep.archived)} low-value record(s). "
            f"threshold={rep.threshold}, min_age_days={rep.min_age_days}",
            file=sys.stderr,
        )
        return True

    print(f"(unknown :hygiene subcommand: '{sub}')", file=sys.stderr)
    return True


def _resolve_workspace_text_file(workspace: Path, raw_path: str, *, role: str) -> Path:
    if not raw_path or not raw_path.isascii():
        raise ValueError(f"{role} must be a non-empty ASCII path")
    p = Path(raw_path)
    if p.is_absolute() or ".." in p.parts:
        raise PermissionError(f"{role} must stay inside the workspace")
    resolved = (workspace / p).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        raise PermissionError(f"{role} resolves outside the workspace") from None
    if not resolved.is_file():
        raise FileNotFoundError(f"{role} not found: {raw_path}")
    return resolved


def _handle_repair(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """`:repair <target_path> <proposal_file> [test_path...] [--pattern PAT]`."""
    tokens = rest.split()
    if len(tokens) < 2:
        print(
            "Usage: :repair <target_path> <proposal_file> [test_path...] [--pattern PAT]\n"
            "Example: :repair core/foo.py tmp/foo.proposed.py tests/test_foo.py",
            file=sys.stderr,
        )
        return True

    pattern: str | None = None
    if "--pattern" in tokens:
        i = tokens.index("--pattern")
        if i + 1 >= len(tokens):
            print("Usage: --pattern requires a value", file=sys.stderr)
            return True
        pattern = tokens[i + 1]
        tokens = tokens[:i] + tokens[i + 2:]

    if len(tokens) < 2:
        print("Usage: :repair <target_path> <proposal_file> [test_path...]", file=sys.stderr)
        return True

    target_path = tokens[0]
    proposal_path = tokens[1]
    test_paths = tuple(tokens[2:] or ["tests"])

    try:
        proposal_file = _resolve_workspace_text_file(
            workspace,
            proposal_path,
            role="proposal_file",
        )
        proposed_content = proposal_file.read_text(encoding="utf-8")
        report = agent.repair(
            RepairProposal(
                path=target_path,
                proposed_content=proposed_content,
                test_paths=test_paths,
                test_pattern=pattern,
                reason=f"REPL :repair from {proposal_path}",
            ),
            workspace_root=workspace,
        )
    except Exception as exc:
        print(f"repair failed before controller run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return True

    print(report.user_summary(), file=sys.stderr)
    return True


def _parse_repair_generation_args(rest: str) -> tuple[str | None, tuple[str, ...], str | None, str | None, str | None]:
    tokens = rest.split()
    if not tokens:
        return None, (), None, None, "Usage: :propose-repair <target_path> [test_path...] [--pattern PAT] [--trace TRACE]"

    pattern: str | None = None
    trace_id: str | None = None
    for flag_name in ("--pattern", "--trace"):
        while flag_name in tokens:
            i = tokens.index(flag_name)
            if i + 1 >= len(tokens):
                return None, (), None, None, f"Usage: {flag_name} requires a value"
            value = tokens[i + 1]
            tokens = tokens[:i] + tokens[i + 2:]
            if flag_name == "--pattern":
                pattern = value
            else:
                trace_id = value

    target_path = tokens[0]
    test_paths = tuple(tokens[1:] or ["tests"])
    return target_path, test_paths, pattern, trace_id, None


def _handle_propose_repair(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    target_path, test_paths, pattern, trace_id, error = _parse_repair_generation_args(rest)
    if error:
        print(error, file=sys.stderr)
        return True

    report = agent.propose_repair(
        target_path=target_path or "",
        workspace_root=workspace,
        test_paths=test_paths,
        test_pattern=pattern,
        trace_id=trace_id,
    )
    print(report.user_summary(), file=sys.stderr)
    return True


def _print_persistent(agent: AgentLoop) -> None:
    records = agent.list_persistent()
    if not records:
        print("(no persistent records)", file=sys.stderr)
        return
    print(f"\n=== persistent memory ({len(records)} records) ===")
    for r in records:
        text = r.content if isinstance(r.content, str) else json.dumps(r.content, ensure_ascii=False)
        if len(text) > 200:
            text = text[:199] + "…"
        tags = ",".join(r.tags) if r.tags else "-"
        print(f"  [{r.id}] type={r.type} tags={tags}")
        print(f"    {text}")


def _handle_smart_memory(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :smart-memory [--json]", file=sys.stderr)
        return True
    payload = agent.smart_memory_summary()
    agent.log.log("smart_memory_status", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    episodic = payload["episodic"]
    procedural = payload["procedural"]
    consolidation = payload["consolidation"]
    print("=== smart memory ===", file=sys.stderr)
    print(
        f"episodic: episodes={episodic['episodes']} "
        f"outcomes={episodic['outcomes']} path={episodic['path']}",
        file=sys.stderr,
    )
    print(
        f"procedural: procedures={procedural['procedures']} "
        f"statuses={procedural['statuses']} path={procedural['path']}",
        file=sys.stderr,
    )
    print(
        f"consolidation: reports={consolidation['reports']} "
        f"path={consolidation['path']}",
        file=sys.stderr,
    )
    last_report = consolidation.get("last_report")
    if last_report:
        notes = "; ".join(last_report.get("notes") or [])
        print(f"last consolidation: {notes}", file=sys.stderr)
    return True


def _handle_memory_consolidate(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :memory-consolidate [--json]", file=sys.stderr)
        return True
    if (
        agent.episodic_store is None
        or agent.procedural_store is None
        or agent.consolidation_store is None
    ):
        print("(smart memory stores are not configured)", file=sys.stderr)
        return True
    report = consolidate_memory(
        episodes=agent.episodic_store.load(),
        procedures=agent.procedural_store.load(),
    )
    agent.consolidation_store.save(report)
    payload = report.to_dict()
    agent.log.log("memory_consolidation", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True
    print("=== memory consolidation ===", file=sys.stderr)
    print(
        f"report={report.id} episodes={report.episode_count} "
        f"procedures={report.procedure_count}",
        file=sys.stderr,
    )
    for note in report.notes:
        print(f"  - {note}", file=sys.stderr)
    return True


def _split_meta_args(rest: str) -> list[str]:
    if not rest.strip():
        return []
    try:
        tokens = shlex.split(rest, posix=False)
    except ValueError:
        tokens = rest.split()
    return [t.strip().strip('"').strip("'") for t in tokens if t.strip()]


def _parse_ingest_options(
    rest: str,
    *,
    default_path: str | None,
) -> tuple[str | None, bool, bool | None, int, str | None]:
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = DEFAULT_PROJECT_LIMIT
    paths: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                return None, dry_run, auto_write, limit, "Usage: --limit requires a number"
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                return None, dry_run, auto_write, limit, "Usage: --limit requires a number"
            if limit < 1:
                return None, dry_run, auto_write, limit, "Usage: --limit must be >= 1"
            i += 2
            continue
        paths.append(token)
        i += 1

    if not paths and default_path is None:
        return None, dry_run, auto_write, limit, "Usage: :ingest-source <path> [--dry-run] [--write-memory|--no-memory]"
    path = " ".join(paths) if paths else default_path
    return path, dry_run, auto_write, limit, None


def _handle_ingest_source(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, _limit, error = _parse_ingest_options(
        rest,
        default_path=None,
    )
    if error or path is None:
        print(error or "Usage: :ingest-source <path>", file=sys.stderr)
        return True
    try:
        report = ingest_source(
            agent=agent,
            workspace=workspace,
            path=path,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_project(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, limit, error = _parse_ingest_options(
        rest,
        default_path=".",
    )
    if error or path is None:
        print(error or "Usage: :ingest-project [path]", file=sys.stderr)
        return True
    try:
        report = ingest_project(
            agent=agent,
            workspace=workspace,
            path=path,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_source_library(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    group: str | None = None
    for token in tokens:
        if token == "--json":
            as_json = True
            continue
        if group is not None:
            print("Usage: :source-library [group|all] [--json]", file=sys.stderr)
            return True
        group = token

    payload = source_library_payload()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    entries = list_source_library()
    if group and group != "all":
        wanted = set(payload["groups"].get(group, [group]))
        entries = tuple(entry for entry in entries if entry.id in wanted)
    print("=== source library ===", file=sys.stderr)
    print("groups: " + ", ".join(sorted(payload["groups"])), file=sys.stderr)
    for entry in entries:
        print(
            f"  {entry.id} [{entry.category}] trust={entry.trust_level:.2f} "
            f"domains={','.join(entry.allowed_domains)}",
            file=sys.stderr,
        )
        print(f"    {entry.description}", file=sys.stderr)
    return True


def _handle_source_registry(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 20
    show_claims = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--claims":
            show_claims = True
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
        print(f"Usage: unknown :source-registry option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True

    store = getattr(agent, "source_registry_store", None)
    registry = store.load_registry() if store is not None else getattr(agent, "last_source_registry", None)
    if registry is None:
        payload = {
            "path": None,
            "sources": 0,
            "claims": 0,
            "items": [],
        }
    else:
        claims_by_source: dict[str, list] = {}
        for claim in registry.claims:
            claims_by_source.setdefault(claim.source_id, []).append(claim)
        sources = list(registry.sources)
        items = []
        for source in sources[:limit]:
            source_claims = claims_by_source.get(source.id, [])
            item = {
                "id": source.id,
                "type": source.type,
                "title": source.title,
                "locator": source.locator,
                "trust_level": source.trust_level,
                "claim_count": len(source_claims),
            }
            if show_claims:
                item["claims"] = [
                    {
                        "id": claim.id,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "locator": claim.locator,
                        "text": claim.text,
                    }
                    for claim in source_claims[:limit]
                ]
            items.append(item)
        payload = {
            "path": str(store.path) if store is not None else None,
            "sources": len(registry.sources),
            "claims": len(registry.claims),
            "shown": len(items),
            "limit": limit,
            "items": items,
        }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== source registry ===", file=sys.stderr)
    if payload["path"]:
        print(f"path: {payload['path']}", file=sys.stderr)
    print(
        f"sources={payload['sources']} claims={payload['claims']} "
        f"shown={payload.get('shown', 0)} limit={payload.get('limit', limit)}",
        file=sys.stderr,
    )
    if not payload["items"]:
        print("(no ingested sources)", file=sys.stderr)
        return True
    for item in payload["items"]:
        print(
            f"  {item['id']} [{item['type']}] claims={item['claim_count']} "
            f"trust={float(item['trust_level']):.2f}",
            file=sys.stderr,
        )
        title = item.get("title") or item.get("locator") or ""
        if title:
            print(f"    {title}", file=sys.stderr)
        if show_claims:
            for claim in item.get("claims", []):
                print(
                    f"    - [{claim['status']} {float(claim['confidence']):.2f}] "
                    f"{claim['text']}",
                    file=sys.stderr,
                )
    return True


def _parse_source_planning_args(rest: str, *, usage: str) -> tuple[bool, int, str] | None:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 8
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print(f"Usage: {usage} --limit requires a number", file=sys.stderr)
                return None
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print(f"Usage: {usage} --limit requires a number", file=sys.stderr)
                return None
            i += 2
            continue
        goal_parts.append(token)
        i += 1
    if limit < 1:
        print(f"Usage: {usage} --limit must be >= 1", file=sys.stderr)
        return None
    return as_json, limit, " ".join(goal_parts).strip()


def _handle_source_review_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":source-review-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _source_review_plan_payload(goal, agent, limit=limit)
    agent.log.log("operator_source_review_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_source_review_plan(payload), file=sys.stderr)
    return True


def _handle_implementation_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":implementation-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _implementation_plan_payload(
        goal,
        agent,
        limit=limit,
        kind="implementation_plan",
    )
    agent.log.log("operator_implementation_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_implementation_plan(payload), file=sys.stderr)
    return True


def _handle_patch_proposal_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":patch-proposal-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _implementation_plan_payload(
        goal,
        agent,
        limit=limit,
        kind="patch_proposal",
    )
    agent.log.log("operator_patch_proposal_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_implementation_plan(payload), file=sys.stderr)
    return True


def _source_review_plan_payload(goal: str, agent: AgentLoop, *, limit: int = 8) -> dict:
    store = getattr(agent, "source_registry_store", None)
    registry = store.load_registry() if store is not None else getattr(agent, "last_source_registry", None)
    if registry is None:
        sources = []
        claims = []
    else:
        sources = list(registry.sources)
        claims = list(registry.claims)
    claims_by_source: dict[str, list] = {}
    for claim in claims:
        claims_by_source.setdefault(claim.source_id, []).append(claim)

    mentions = _extract_source_review_mentions(goal)
    matched_sources = _match_sources_to_mentions(sources, mentions)
    if not mentions and sources:
        matched_sources = sources[:limit]
    if mentions:
        matched_ids = {source.id for source in matched_sources}
        missing_mentions = [
            mention for mention in mentions
            if not any(_mention_matches_source(mention, source) for source in matched_sources)
        ]
    else:
        matched_ids = {source.id for source in matched_sources}
        missing_mentions = []

    items = []
    for source in matched_sources[:limit]:
        source_claims = claims_by_source.get(source.id, [])
        items.append({
            "id": source.id,
            "type": source.type,
            "title": source.title,
            "locator": source.locator,
            "claim_count": len(source_claims),
            "sample_claims": [
                {
                    "status": claim.status,
                    "confidence": claim.confidence,
                    "text": claim.text,
                }
                for claim in source_claims[:2]
            ],
        })

    suggested_files = _suggest_implementation_files(matched_sources, mentions)
    return {
        "goal": goal or "source review implementation plan",
        "registry": {
            "path": str(store.path) if store is not None else None,
            "sources": len(sources),
            "claims": len(claims),
        },
        "requested_mentions": mentions,
        "matched_sources": items,
        "missing_mentions": missing_mentions,
        "suggested_files": suggested_files,
        "plan": [
            "Use only matched Source Registry entries as current evidence; inspect missing files explicitly before claiming they were reviewed.",
            "Confirm the intended behavior from the task/source claims, then map each change to one small code surface.",
            "Patch routing/handler code first, then add regression tests for positive routing and negative over-routing.",
            "Run targeted tests for operator intent/CLI, then the full pytest suite before commit.",
        ],
        "tests_to_add": [
            "source-review planning request routes to source_review_plan, not project_health",
            "source review plan lists matched ingested sources and missing requested sources",
            "handler does not call planner/LLM for this operator digest",
            "existing project health and next-actions phrases still route correctly",
        ],
        "constraints": [
            "read-only planning digest",
            "no file writes",
            "no shell execution",
            "no autonomous allow-effects",
            "no persistent memory promotion",
        ],
        "matched_source_ids": sorted(matched_ids),
    }


def _implementation_plan_payload(
    goal: str,
    agent: AgentLoop,
    *,
    limit: int = 8,
    kind: str = "implementation_plan",
) -> dict:
    source_review = _source_review_plan_payload(goal, agent, limit=limit)
    if not source_review.get("requested_mentions"):
        source_review = {
            **source_review,
            "matched_sources": [],
            "matched_source_ids": [],
            "suggested_files": [
                "Use explicit multi-file review mode or :ingest-source to inspect the relevant files before patching."
            ],
        }
    suggested_files = source_review.get("suggested_files", [])
    implementation_steps = _implementation_steps_for_kind(kind)
    return {
        "kind": kind,
        "goal": goal or ("patch proposal plan" if kind == "patch_proposal" else "implementation plan"),
        "source_evidence": {
            "registry": source_review.get("registry", {}),
            "requested_mentions": source_review.get("requested_mentions", []),
            "matched_sources": source_review.get("matched_sources", []),
            "missing_mentions": source_review.get("missing_mentions", []),
            "matched_source_ids": source_review.get("matched_source_ids", []),
        },
        "files_functions_to_inspect_or_change": suggested_files,
        "implementation_steps": implementation_steps,
        "tests_to_add": _implementation_tests_for_kind(kind),
        "approval_boundary": [
            "This report is read-only planning.",
            "Do not edit files, run shell commands, apply repair, or enable allow-effects from this step.",
            "Require explicit approval before any file_write, shell_exec, repair apply, rollback, or persistent memory promotion.",
        ],
        "risks": [
            "Missing source mentions are not verified and must be read explicitly before patching.",
            "A plan based only on registry claims may be stale if code changed after ingestion.",
            "Routing changes are high leverage; keep positive and negative regression tests close to the change.",
        ],
        "constraints": [
            "local deterministic operator report",
            "no LLM required",
            "no web",
            "no file writes",
            "no shell execution",
            "no autonomous allow-effects",
        ],
    }


def _implementation_steps_for_kind(kind: str) -> list[str]:
    if kind == "patch_proposal":
        return [
            "Collect explicit file evidence for every target file before proposing a diff.",
            "Identify the smallest behavioral mismatch and map it to one code surface.",
            "Draft a minimal patch proposal with expected before/after behavior.",
            "Name targeted tests and rollback expectations, then stop before applying.",
        ]
    return [
        "Separate source evidence from implementation assumptions.",
        "Map each requested behavior to the smallest likely file/function surface.",
        "Add or adjust deterministic routing/handler code before any broad refactor.",
        "Add positive routing tests and negative over-routing tests.",
        "Run targeted tests first, then full pytest before commit.",
    ]


def _implementation_tests_for_kind(kind: str) -> list[str]:
    tests = [
        "source-review requests route to source_review_plan, not project_health",
        "implementation-plan requests route to implementation_plan, not source_review_plan",
        "handler returns a local report without planner/LLM calls",
    ]
    if kind == "patch_proposal":
        tests.append("patch proposal requests return a patch proposal plan, not a generic source review")
    else:
        tests.append("implementation plan report lists files/functions, tests, risks and approval boundary")
    return tests


def _format_implementation_plan(payload: dict) -> str:
    kind = payload.get("kind")
    title = "patch proposal plan" if kind == "patch_proposal" else "implementation plan"
    evidence = payload.get("source_evidence", {})
    registry = evidence.get("registry", {})
    lines = [
        f"=== {title} ===",
        f"kind: {kind}",
        f"goal: {payload.get('goal')}",
        (
            "source evidence: "
            f"sources={registry.get('sources', 0)} "
            f"claims={registry.get('claims', 0)} "
            f"matched={len(evidence.get('matched_sources', []))} "
            f"missing={len(evidence.get('missing_mentions', []))}"
        ),
    ]
    requested = evidence.get("requested_mentions", [])
    if requested:
        lines.append("requested sources/files:")
        lines.extend(f"  - {item}" for item in requested)
    matched = evidence.get("matched_sources", [])
    if matched:
        lines.append("matched evidence:")
        for item in matched:
            lines.append(
                f"  - {item.get('id')} [{item.get('type')}] claims={item.get('claim_count', 0)}"
            )
    missing = evidence.get("missing_mentions", [])
    if missing:
        lines.append("not verified from registry:")
        lines.extend(f"  - {item}" for item in missing)
    lines.append("files/functions to inspect or change:")
    lines.extend(f"  - {item}" for item in payload.get("files_functions_to_inspect_or_change", []))
    lines.append("implementation steps:")
    lines.extend(
        f"  {idx}. {item}"
        for idx, item in enumerate(payload.get("implementation_steps", []), start=1)
    )
    lines.append("tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("risks:")
    lines.extend(f"  - {item}" for item in payload.get("risks", []))
    lines.append("approval boundary:")
    lines.extend(f"  - {item}" for item in payload.get("approval_boundary", []))
    lines.append("constraints:")
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    return "\n".join(lines)


def _format_source_review_plan(payload: dict) -> str:
    registry = payload.get("registry", {})
    lines = [
        "=== source review plan ===",
        f"goal: {payload.get('goal')}",
        (
            "registry: "
            f"sources={registry.get('sources', 0)} "
            f"claims={registry.get('claims', 0)}"
        ),
    ]
    mentions = payload.get("requested_mentions", [])
    if mentions:
        lines.append("requested sources:")
        lines.extend(f"  - {item}" for item in mentions)
    matched = payload.get("matched_sources", [])
    if matched:
        lines.append("matched ingested sources:")
        for item in matched:
            lines.append(
                f"  - {item.get('id')} [{item.get('type')}] "
                f"claims={item.get('claim_count', 0)}"
            )
            for claim in item.get("sample_claims", []):
                lines.append(
                    f"    claim[{claim.get('status')} {float(claim.get('confidence', 0)):.2f}]: "
                    f"{claim.get('text')}"
                )
    else:
        lines.append("matched ingested sources: none")
    missing = payload.get("missing_mentions", [])
    if missing:
        lines.append("not verified from registry:")
        lines.extend(f"  - {item}" for item in missing)
    suggested = payload.get("suggested_files", [])
    if suggested:
        lines.append("likely files to inspect/change:")
        lines.extend(f"  - {item}" for item in suggested)
    lines.append("implementation plan:")
    lines.extend(f"  {idx}. {step}" for idx, step in enumerate(payload.get("plan", []), start=1))
    lines.append("tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("constraints:")
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    return "\n".join(lines)


def _extract_source_review_mentions(text: str) -> list[str]:
    pattern = re.compile(
        r"(?P<path>"
        r"(?:[A-Za-z]:[\\/])?"
        r"(?:\.{1,2}[\\/])?"
        r"(?:[A-Za-z0-9_.-]+[\\/])*"
        r"[A-Za-z0-9_.-]+\."
        r"(?:py|md|txt|json|yml|yaml|pdf)"
        r")",
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    mentions: list[str] = []
    for match in pattern.finditer(text):
        value = match.group("path").rstrip(".,;:!?)\"]}'")
        key = _normalize_source_key(value)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(value)
    return mentions


def _match_sources_to_mentions(sources: list, mentions: list[str]) -> list:
    if not mentions:
        return []
    matched = []
    seen: set[str] = set()
    for source in sources:
        if any(_mention_matches_source(mention, source) for mention in mentions):
            key = _source_dedupe_key(source)
            if key not in seen:
                matched.append(source)
                seen.add(key)
    return matched


def _mention_matches_source(mention: str, source) -> bool:
    mention_key = _normalize_source_key(mention)
    candidates = [
        getattr(source, "id", ""),
        getattr(source, "title", ""),
        getattr(source, "locator", ""),
    ]
    for candidate in candidates:
        key = _normalize_source_key(str(candidate))
        if mention_key == key or mention_key in key or key.endswith(mention_key):
            return True
    return False


def _normalize_source_key(value: str) -> str:
    out = value.strip().strip("\"'")
    if out.startswith("file:"):
        out = out[len("file:"):]
    out = out.replace("/", "\\")
    while out.startswith(".\\"):
        out = out[2:]
    return out.casefold()


def _source_dedupe_key(source) -> str:
    for value in (
        getattr(source, "locator", ""),
        getattr(source, "title", ""),
        getattr(source, "id", ""),
    ):
        key = _normalize_source_key(str(value))
        if key:
            return key
    return str(getattr(source, "id", "")).casefold()


def _suggest_implementation_files(sources: list, mentions: list[str]) -> list[str]:
    names = {_normalize_source_key(item) for item in mentions}
    for source in sources:
        for value in (getattr(source, "id", ""), getattr(source, "locator", ""), getattr(source, "title", "")):
            key = _normalize_source_key(str(value))
            if key:
                names.add(key)
    suggestions: list[str] = []
    if any("core\\operator_intent.py" in name or "operator_intent.py" == name for name in names):
        suggestions.append("core/operator_intent.py - route source-review and implementation-plan language")
    if any("main.py" in name for name in names):
        suggestions.append("main.py - dispatch the operator source-review plan command")
    if any("test" in name for name in names) or suggestions:
        suggestions.append("tests/test_operator_intent.py - routing regressions")
        suggestions.append("tests/test_cli.py - command/handler regressions")
    if not suggestions:
        suggestions.append("Use explicit multi-file review mode or :ingest-source to inspect the relevant files before patching.")
    return suggestions


def _handle_ingest_web(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 5
    per_source = 1
    source_selection: str | None = None
    topic_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--sources":
            if i + 1 >= len(tokens):
                print("Usage: --sources requires a comma-separated list or group", file=sys.stderr)
                return True
            source_selection = tokens[i + 1]
            i += 2
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
        if token == "--per-source":
            if i + 1 >= len(tokens):
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            try:
                per_source = int(tokens[i + 1])
            except ValueError:
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        topic_parts.append(token)
        i += 1

    topic = " ".join(topic_parts).strip()
    if not topic:
        print(
            "Usage: :ingest-web <topic> [--sources wikis|books|science|docs|all|id,id] "
            "[--limit N] [--per-source N] [--dry-run] [--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_web_topic(
            agent=agent,
            topic=topic,
            source_selection=source_selection,
            limit=limit,
            per_source=per_source,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest web failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_rss(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 10
    url_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
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
        url_parts.append(token)
        i += 1

    url = " ".join(url_parts).strip()
    if not url:
        print(
            "Usage: :ingest-rss <feed_url> [--limit N] [--dry-run] "
            "[--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_rss_feed(
            agent=agent,
            url=url,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest rss failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_connectors(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    status = "all"
    for token in tokens:
        if token == "--json":
            as_json = True
            continue
        if status != "all":
            print("Usage: :connectors [all|wired|partial|planned] [--json]", file=sys.stderr)
            return True
        status = token.lower()
    if status not in {"all", "wired", "partial", "planned"}:
        print("Usage: :connectors [all|wired|partial|planned] [--json]", file=sys.stderr)
        return True

    registry = SourceConnectorRegistry()
    if as_json:
        payload = source_connector_payload()
        if status != "all":
            payload["connectors"] = [
                item for item in payload["connectors"] if item["status"] == status
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== source connectors ===", file=sys.stderr)
    for connector in registry.list(status=status):
        cost = connector.cost.to_dict()
        print(
            f"  {connector.id} [{connector.status}] cost={cost['cost_class']} "
            f"auth={connector.requires_auth} network={connector.network}",
            file=sys.stderr,
        )
        print(
            "    commands="
            + (", ".join(connector.commands) if connector.commands else "-"),
            file=sys.stderr,
        )
        print(f"    use: {'; '.join(connector.use_when)}", file=sys.stderr)
    return True


def _handle_connector_plan(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 4
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
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
        goal_parts.append(token)
        i += 1

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :connector-plan <goal> [--limit N] [--json]", file=sys.stderr)
        return True
    plan = plan_source_connectors(goal, limit=limit)
    if as_json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(plan.user_summary(), file=sys.stderr)
    return True


def _handle_models(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :models [--json]", file=sys.stderr)
        return True

    from core.model_catalog import catalog_summary
    payload = {
        "routes":   agent.model_router.routing_summary(),
        "registry": agent.model_router.registry_summary(),
        "catalog":  catalog_summary(),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== model routes ===", file=sys.stderr)
    for role, route in payload["routes"].items():
        print(
            f"  {role}: provider={route.get('provider')} "
            f"model={route.get('model')} reason={route.get('reason')}",
            file=sys.stderr,
        )
    policy = payload["registry"].get("selection_policy", {})
    print("=== model selection policy ===", file=sys.stderr)
    print(
        f"  name={policy.get('name')} max_cost={policy.get('max_cost_tier')} "
        f"allow_mock={policy.get('allow_mock')} "
        f"require_available={policy.get('require_available')}",
        file=sys.stderr,
    )
    print("=== model registry ===", file=sys.stderr)
    for spec in payload["registry"]["models"]:
        print(
            f"  {spec['id']} provider={spec['provider']} model={spec['model']} "
            f"roles={','.join(spec['roles'])} cost={spec['cost_tier']} "
            f"quality={spec['quality_tier']} supported={spec['provider_supported']} "
            f"available={spec['available']} source={spec['source']}",
            file=sys.stderr,
        )
    cat = payload["catalog"]
    if cat.get("status") == "no_catalog":
        print("=== model catalog === (not populated — run :refresh-models)", file=sys.stderr)
    else:
        print(f"=== model catalog (updated {cat.get('updated_at', '?')}) ===", file=sys.stderr)
        for provider, pdata in cat.get("providers", {}).items():
            tiers = pdata.get("tier_best", {})
            n = pdata.get("model_count", 0)
            tier_str = "  ".join(f"{t}={m}" for t, m in sorted(tiers.items()))
            print(f"  {provider} ({n} models): {tier_str}", file=sys.stderr)
    return True


def _handle_model_registry_audit(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :model-registry-audit [--json]", file=sys.stderr)
        return True
    audit = audit_model_registry(agent.model_router)
    agent.log.log("model_registry_audit", audit.to_dict())
    if as_json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(audit.user_summary(), file=sys.stderr)
    return True


def _handle_refresh_models(rest: str, agent: AgentLoop) -> bool:
    """Query provider APIs, classify models into tiers, write config/model_catalog.json.

    Usage: :refresh-models [--anthropic] [--openai]

    The catalog is queried from the provider's own API (not web search).
    Results are classified by naming pattern (haiku→light, opus→deep, …)
    and saved to config/model_catalog.json. No model names are hardcoded.
    """
    from core.model_catalog import catalog_summary, classify_model, refresh_catalog
    from core.task_complexity import ComplexityTier

    tokens = _split_meta_args(rest)
    requested = [t.lstrip("-") for t in tokens if t.startswith("--")]
    providers = requested if requested else None  # None = all available

    print("\n(refresh-models: запрашиваю актуальный список моделей у провайдеров...)", file=sys.stderr)

    try:
        catalog = refresh_catalog(providers=providers)
    except Exception as exc:  # noqa: BLE001
        print(f"(refresh-models ошибка: {exc})", file=sys.stderr)
        print("  Проверьте ANTHROPIC_API_KEY / OPENAI_API_KEY в .env", file=sys.stderr)
        return True

    print("\n── Результат ────────────────────────────────────────────────────────", file=sys.stderr)
    for provider, pdata in catalog.get("providers", {}).items():
        print(f"\n  {provider.upper()}", file=sys.stderr)
        tier_best = pdata.get("tier_best", {})
        all_models = pdata.get("models", [])
        for tier in ComplexityTier:
            best = tier_best.get(tier.value, "(не найдено)")
            candidates = [m["id"] for m in all_models if m["tier"] == tier.value]
            print(f"    {tier.value:<8} → {best}", file=sys.stderr)
            if len(candidates) > 1:
                others = [m for m in candidates if m != best]
                print(f"             (другие: {', '.join(others[:4])})", file=sys.stderr)

    print("\n── Каталог сохранён → config/model_catalog.json ────────────────────", file=sys.stderr)
    print("  Агент автоматически использует эти модели при следующем запуске.", file=sys.stderr)
    print("  Для override задайте в .env:", file=sys.stderr)
    print("    AGENT_MODEL_TIER_LIGHT    = <model-id>", file=sys.stderr)
    print("    AGENT_MODEL_TIER_STANDARD = <model-id>", file=sys.stderr)
    print("    AGENT_MODEL_TIER_DEEP     = <model-id>", file=sys.stderr)
    return True


def _handle_architecture_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 8
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
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
        print(f"Usage: unknown :architecture-audit option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    audit = audit_architecture(workspace)
    agent.log.log("architecture_audit", audit.to_dict())
    if as_json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(audit.user_summary(limit=limit), file=sys.stderr)
    return True


def _handle_learn(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = DEFAULT_PROJECT_LIMIT
    root = "."
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
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
        if token == "--root":
            if i + 1 >= len(tokens):
                print("Usage: --root requires a path", file=sys.stderr)
                return True
            root = tokens[i + 1]
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    goal = " ".join(goal_parts).strip()
    try:
        plan = LearningPlanner().plan(
            workspace=workspace,
            goal=goal,
            root=root,
            limit=limit,
        )
        agent.log.log("learning_plan", plan.to_log_payload())
        print(plan.user_summary(), file=sys.stderr)
        if not plan.source_paths:
            return True
        report = ingest_files(
            agent=agent,
            workspace=workspace,
            paths=plan.source_paths,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(learn failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _approval_inbox_for(agent: AgentLoop, workspace: Path | None = None) -> ApprovalInbox:
    inbox = getattr(agent, "approval_inbox", None)
    if inbox is None:
        path = (workspace / DEFAULT_APPROVAL_INBOX_PATH) if workspace is not None else None
        inbox = ApprovalInbox(path=path)
        setattr(agent, "approval_inbox", inbox)
    return inbox


def _handle_auto_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    dry_run = True
    include_tests = True
    include_goal = False
    enable_reflection = False
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
    dry_run = True
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
        if token == "--max-cycles":
            if i + 1 >= len(tokens):
                print("Usage: --max-cycles requires a number", file=sys.stderr)
                return True
            try:
                max_cycles = int(tokens[i + 1])
            except ValueError:
                print("Usage: --max-cycles requires an integer", file=sys.stderr)
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


_BUDGET_ENV_VARS = (
    "AGENT_BUDGET_HOUR_LLM_CALLS",
    "AGENT_BUDGET_HOUR_MODEL_TOKENS",
    "AGENT_BUDGET_HOUR_MODEL_COST_UNITS",
    "AGENT_BUDGET_HOUR_WEB_FETCHES",
    "AGENT_BUDGET_DAY_LLM_CALLS",
    "AGENT_BUDGET_DAY_MODEL_TOKENS",
    "AGENT_BUDGET_DAY_MODEL_COST_UNITS",
    "AGENT_BUDGET_DAY_WEB_FETCHES",
)


def _handle_budget_config(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :budget-config [--json]", file=sys.stderr)
        return True
    payload = _budget_config_payload(agent, workspace)
    agent.log.log("operator_budget_config", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_budget_config(payload), file=sys.stderr)
    return True


def _budget_config_payload(agent: AgentLoop, workspace: Path) -> dict:
    snapshot = _budget_ledger_snapshot(agent)
    config_path = None
    if isinstance(snapshot, dict):
        config_path = snapshot.get("config_path")
    if not config_path:
        config_path = str((workspace / "config" / "budget_limits.json").resolve())
    config_file = Path(str(config_path))
    budget_policy = _budget_enforcement_status(snapshot)
    windows = snapshot.get("windows", []) if isinstance(snapshot, dict) else []
    return {
        "config_path": str(config_file),
        "config_exists": config_file.exists(),
        "example_path": str((workspace / "config" / "budget_limits.example.json").resolve()),
        "env_overrides": {
            name: {
                "set": bool(os.getenv(name)),
                "value": os.getenv(name) or "",
            }
            for name in _BUDGET_ENV_VARS
        },
        "effective_windows": windows,
        "budget_policy": budget_policy,
        "powershell_example": [
            '$env:AGENT_BUDGET_HOUR_LLM_CALLS="20"',
            '$env:AGENT_BUDGET_DAY_LLM_CALLS="100"',
            '$env:AGENT_BUDGET_DAY_MODEL_TOKENS="300000"',
            '$env:AGENT_BUDGET_DAY_MODEL_COST_UNITS="500"',
        ],
    }


def _format_budget_config(payload: dict) -> str:
    policy = payload.get("budget_policy") or {}
    lines = [
        "=== budget config ===",
        f"config_path: {payload.get('config_path')}",
        f"config_exists: {payload.get('config_exists')}",
        f"example_path: {payload.get('example_path')}",
        (
            "effective enforcement: "
            f"tracking={policy.get('tracking_enabled')} "
            f"limits_configured={policy.get('enforcement_enabled')} "
            f"usage_recorded={policy.get('usage_recorded')}"
        ),
    ]
    warning = policy.get("warning")
    if warning:
        lines.append("budget warning:")
        lines.append(f"  - {warning}")
    lines.append("effective windows:")
    windows = payload.get("effective_windows") or []
    if windows:
        for window in windows:
            counters = window.get("counters", {})
            configured = [
                f"{name}={data.get('used', 0)}/{data.get('limit', 0)}"
                for name, data in counters.items()
                if isinstance(data, dict)
                and (int(data.get("used", 0) or 0) or int(data.get("limit", 0) or 0))
            ]
            lines.append(
                f"  - {window.get('name')}: "
                + (", ".join(configured) if configured else "all limits 0")
            )
    else:
        lines.append("  - none")
    env_overrides = payload.get("env_overrides") or {}
    set_env = [name for name, info in env_overrides.items() if info.get("set")]
    lines.append("env overrides:")
    lines.extend(f"  - {name}=set" for name in set_env[:8])
    if not set_env:
        lines.append("  - none")
    if not policy.get("enforcement_enabled"):
        lines.append("PowerShell quick start:")
        lines.extend(f"  {item}" for item in payload.get("powershell_example", []))
    return "\n".join(lines)


def _format_operator_budget_digest(payload: dict) -> str:
    model_usage = payload.get("model_usage") or {}
    limits = model_usage.get("limits", {})
    totals = model_usage.get("totals", {})
    session = model_usage.get("session_totals", {})
    windows_payload = payload.get("persistent_budget_windows") or {}
    windows = windows_payload.get("windows", []) if isinstance(windows_payload, dict) else []
    budget_policy = payload.get("budget_policy") or _budget_enforcement_status(
        windows_payload if isinstance(windows_payload, dict) else None
    )
    lines = [
        "=== operator budget ===",
        (
            "model usage: "
            f"history_calls={totals.get('calls', 0)} "
            f"history_tokens={totals.get('total_tokens', 0)} "
            f"history_cost_units={totals.get('cost_units', 0)}"
        ),
        (
            "this session: "
            f"calls={session.get('calls', 0)} "
            f"tokens={session.get('total_tokens', 0)} "
            f"cost_units={session.get('cost_units', 0)}"
        ),
        (
            "session caps: "
            f"max_calls={limits.get('max_calls', 0)} "
            f"max_tokens={limits.get('max_tokens', 0)} "
            f"max_cost_units={limits.get('max_cost_units', 0)}"
        ),
        (
            "persistent enforcement: "
            f"tracking={budget_policy.get('tracking_enabled')} "
            f"limits_configured={budget_policy.get('enforcement_enabled')} "
            f"usage_recorded={budget_policy.get('usage_recorded')}"
        ),
    ]
    if budget_policy.get("warning"):
        lines.append("budget warning:")
        lines.append(f"  - {budget_policy['warning']}")
    if windows:
        lines.append("persistent windows:")
        for window in windows:
            counters = window.get("counters", {})
            compact = ", ".join(
                f"{name}={data.get('used', 0)}/{data.get('limit', 0)}"
                for name, data in counters.items()
                if isinstance(data, dict)
            )
            lines.append(f"  - {window.get('name')}: {compact}")
    else:
        lines.append("persistent windows: not enabled")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("budget recommendations:")
        for item in recommendations:
            lines.append(f"  - {item}")
    return "\n".join(lines)


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


def _autonomy_readiness_payload(payload: dict) -> dict:
    approvals = payload.get("runtime", {}).get("approval_inbox", {})
    architecture = payload.get("architecture", {})
    pending = int(approvals.get("pending", 0) or 0)
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    budget_limits_configured = bool(budget_policy.get("enforcement_enabled"))
    blockers: list[str] = []
    if pending:
        blockers.append(f"{pending} approval item(s) pending")
    if not budget_limits_configured:
        if budget_policy.get("tracking_enabled"):
            blockers.append(
                "persistent budget tracking is active but enforcement is disabled"
            )
        else:
            blockers.append("persistent hour/day budget windows are not enabled")
    elif budget_policy.get("over_limit"):
        blockers.append("persistent budget usage is already at or above a configured limit")
    if not architecture.get("ready_for_multi_agent_execution"):
        blockers.append("multi-agent/long-session readiness gaps remain")
    state = "ready" if not blockers else "limited"
    return {
        "state": state,
        "dry_run_runtime_ready": True,
        "ready_for_multi_agent_execution": bool(
            architecture.get("ready_for_multi_agent_execution")
        ),
        "approvals_pending": pending,
        "persistent_budget_limits_configured": budget_limits_configured,
        "budget_policy": budget_policy,
        "blockers": blockers,
        "recommendations": payload.get("recommendations", []),
    }


def _budget_enforcement_status(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {
            "tracking_enabled": False,
            "enforcement_enabled": False,
            "usage_recorded": False,
            "all_limits_zero": True,
            "over_limit": False,
            "limit_breaches": [],
            "warning": (
                "Persistent budget ledger is not enabled; long unattended "
                "sessions have no cross-run spend stop."
            ),
        }
    windows_raw = snapshot.get("windows", [])
    windows = windows_raw if isinstance(windows_raw, list) else []
    counters = [
        counter
        for window in windows
        if isinstance(window, dict)
        for counter in (window.get("counters", {}) or {}).values()
        if isinstance(counter, dict)
    ]
    totals = snapshot.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}
    usage_recorded = any(
        _budget_int(counter.get("used", 0)) > 0 for counter in counters
    ) or any(_budget_int(value) > 0 for value in totals.values())
    enforcement_enabled = any(
        _budget_int(counter.get("limit", 0)) > 0 for counter in counters
    )
    breaches = [
        {
            "used": _budget_int(counter.get("used", 0)),
            "limit": _budget_int(counter.get("limit", 0)),
        }
        for counter in counters
        if _budget_int(counter.get("limit", 0)) > 0
        and _budget_int(counter.get("used", 0)) >= _budget_int(counter.get("limit", 0))
    ]
    tracking_enabled = bool(windows)
    all_limits_zero = tracking_enabled and not enforcement_enabled
    warning = ""
    if all_limits_zero:
        warning = (
            "Persistent budget tracking is active, but all hour/day limits are 0, "
            "so cross-run enforcement is disabled; set AGENT_BUDGET_HOUR_LLM_CALLS, "
            "AGENT_BUDGET_DAY_MODEL_TOKENS, or AGENT_BUDGET_DAY_MODEL_COST_UNITS "
            "before long unattended sessions."
        )
    elif not tracking_enabled:
        warning = (
            "Persistent budget ledger is not enabled; long unattended sessions "
            "have no cross-run spend stop."
        )
    elif breaches:
        warning = (
            "One or more persistent budget counters are already at or above "
            "their configured limit; the next matching expensive action will "
            "be blocked until the hour/day window rolls forward or limits are raised."
        )
    return {
        "tracking_enabled": tracking_enabled,
        "enforcement_enabled": enforcement_enabled,
        "usage_recorded": usage_recorded,
        "all_limits_zero": all_limits_zero,
        "over_limit": bool(breaches),
        "limit_breaches": breaches,
        "warning": warning,
    }


def _budget_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _persistent_budget_limits_configured(snapshot: dict | None) -> bool:
    return bool(_budget_enforcement_status(snapshot).get("enforcement_enabled"))


def _next_action_prerequisites(payload: dict) -> list[str]:
    prerequisites: list[str] = []
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    if budget_policy.get("warning"):
        prerequisites.append(str(budget_policy["warning"]))

    gaps = payload.get("architecture", {}).get("priority_gaps", [])
    has_long_work_gap = any(
        "long work session" in str(gap.get("title", "")).casefold()
        for gap in gaps
    )
    if has_long_work_gap:
        prerequisites.append(
            "Run a live state-store recovery drill before enabling Long Work Session Mode."
        )
    return prerequisites


def _format_autonomy_readiness(payload: dict) -> str:
    lines = [
        "=== autonomy readiness ===",
        f"state={payload.get('state')}",
        f"dry_run_runtime_ready={payload.get('dry_run_runtime_ready')}",
        f"ready_for_multi_agent_execution={payload.get('ready_for_multi_agent_execution')}",
        f"approvals_pending={payload.get('approvals_pending')}",
        (
            "persistent_budget_limits_configured="
            f"{payload.get('persistent_budget_limits_configured')}"
        ),
    ]
    budget_policy = payload.get("budget_policy") or {}
    if budget_policy.get("warning"):
        lines.append("budget warning:")
        lines.append(f"  - {budget_policy['warning']}")
    blockers = payload.get("blockers", [])
    if blockers:
        lines.append("blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    else:
        lines.append("blockers: none")
    return "\n".join(lines)


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


def _compact_one_line(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


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


def _print_daemon_inbox_notice(workspace: Path) -> None:
    """Check the approval inbox and print a wake-up notice if items are pending.

    Called once at REPL startup so the user immediately sees anything the
    background daemon (agent_tick.py) found while they were away.
    """
    try:
        from core.approval_inbox import ApprovalInbox
        inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
        pending = inbox.pending()
        if not pending:
            return
        print(
            f"\n{'='*60}",
            file=sys.stderr,
        )
        print(
            f"  DAEMON NOTICE: {len(pending)} item(s) found while you were away",
            file=sys.stderr,
        )
        print(f"{'='*60}", file=sys.stderr)
        for item in pending:
            print(f"\n  [{item.id}]  {item.operation}", file=sys.stderr)
            for line in item.summary.splitlines():
                print(f"    {line}", file=sys.stderr)
            if item.reasons:
                print(f"    reason: {'; '.join(item.reasons)}", file=sys.stderr)
        print(
            f"\n  Use :approval-list to review  |  :approval-run to act\n{'='*60}\n",
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


def _handle_conflicts(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    limit = 10
    as_json = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
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
        print(f"Usage: unknown :conflicts option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True

    registry = None
    store = getattr(agent, "source_registry_store", None)
    if store is not None:
        registry = store.load_registry()
    if registry is None or (not registry.sources and not registry.claims):
        registry = getattr(agent, "last_source_registry", None)
    if registry is None:
        print("=== conflicts ===\n(no source registry available)", file=sys.stderr)
        return True

    report = ConflictReview().review(registry)
    agent.log.log("conflict_review", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(limit=limit), file=sys.stderr)
    return True


def _handle_budget_status(agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    snapshot = BudgetGovernor(BudgetLimits()).snapshot()
    snapshot["model_usage"] = agent.model_router.usage_snapshot()
    snapshot["persistent_budget_windows"] = _budget_ledger_snapshot(agent)
    print("=== autonomous budget defaults ===", file=sys.stderr)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _budget_ledger_for(agent: AgentLoop) -> BudgetLedger | None:
    usage_ledger = getattr(agent.model_router, "usage_ledger", None)
    return getattr(usage_ledger, "budget_ledger", None)


def _budget_ledger_snapshot(agent: AgentLoop) -> dict | None:
    ledger = _budget_ledger_for(agent)
    if ledger is None:
        return None
    return ledger.snapshot()


def _handle_budget_window_status(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :budget-window-status [--json]", file=sys.stderr)
        return True
    ledger = _budget_ledger_for(agent)
    if ledger is None:
        print("(persistent budget ledger is not enabled)", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(ledger.snapshot(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(ledger.user_summary(), file=sys.stderr)
    return True


def _handle_state_store_drill(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :state-store-drill [--json]", file=sys.stderr)
        return True
    report = run_state_store_drill(workspace)
    agent.log.log("state_store_recovery_drill", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True


def _handle_release_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :release-audit [--json]", file=sys.stderr)
        return True
    report = build_release_manifest(workspace).report()
    agent.log.log("release_hygiene", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True


def _handle_supply_chain_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :supply-chain-audit [--json]", file=sys.stderr)
        return True
    report = audit_supply_chain(workspace)
    agent.log.log("supply_chain_audit", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True


def _handle_model_usage(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :model-usage [--json]", file=sys.stderr)
        return True
    snapshot = agent.model_router.usage_snapshot()
    if snapshot is None:
        print("(model usage ledger is not enabled)", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=sys.stderr)
        return True
    totals = snapshot.get("totals", {})
    session_totals = snapshot.get("session_totals", {})
    print("=== model usage ===", file=sys.stderr)
    print(
        f"  history_calls={totals.get('calls', 0)} "
        f"history_tokens={totals.get('total_tokens', 0)} "
        f"history_cost_units={totals.get('cost_units', 0)}",
        file=sys.stderr,
    )
    print(
        f"  session_calls={session_totals.get('calls', 0)} "
        f"session_tokens={session_totals.get('total_tokens', 0)} "
        f"session_cost_units={session_totals.get('cost_units', 0)}",
        file=sys.stderr,
    )
    print("=== by role ===", file=sys.stderr)
    for role, data in snapshot.get("by_role", {}).items():
        print(
            f"  {role}: calls={data.get('calls', 0)} "
            f"tokens={data.get('total_tokens', 0)} "
            f"cost_units={data.get('cost_units', 0)}",
            file=sys.stderr,
        )
    print("=== by model ===", file=sys.stderr)
    for model, data in snapshot.get("by_model", {}).items():
        print(
            f"  {model}: calls={data.get('calls', 0)} "
            f"tokens={data.get('total_tokens', 0)} "
            f"cost_units={data.get('cost_units', 0)}",
            file=sys.stderr,
        )
    return True


def _handle_team_plan(rest: str, agent: AgentLoop) -> bool:
    del agent
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 5
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
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
        goal_parts.append(token)
        i += 1
    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :team-plan <goal> [--limit N] [--json]", file=sys.stderr)
        return True
    try:
        plan = TeamPlanner().plan(goal, limit=limit)
    except ValueError as exc:
        print(f"Usage: {exc}", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(plan.user_summary(), file=sys.stderr)
    return True


def _handle_team_run(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    dry_run = True
    limit = 5
    max_model_calls = 10
    max_cost_units = 20
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
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
        if token == "--max-model-calls":
            if i + 1 >= len(tokens):
                print("Usage: --max-model-calls requires a number", file=sys.stderr)
                return True
            try:
                max_model_calls = int(tokens[i + 1])
            except ValueError:
                print("Usage: --max-model-calls requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--max-cost-units":
            if i + 1 >= len(tokens):
                print("Usage: --max-cost-units requires a number", file=sys.stderr)
                return True
            try:
                max_cost_units = int(tokens[i + 1])
            except ValueError:
                print("Usage: --max-cost-units requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1
    goal = " ".join(goal_parts).strip()
    if not goal:
        print(
            "Usage: :team-run <goal> [--allow-effects] [--limit N] "
            "[--max-model-calls N] [--max-cost-units N] [--json]",
            file=sys.stderr,
        )
        return True
    try:
        from core.subagent_runner import SubAgentRunner  # noqa: PLC0415
        runner = None if dry_run else SubAgentRunner(
            workspace_root=Path("."),
            policy=agent.policy,
            model_router=agent.model_router,
            parent_registry=agent.registry,
            log_dir=agent.log.log_dir,
        )
        plan = TeamPlanner().plan(goal, limit=limit)
        executor = TeamExecutor(runner=runner)
        report = executor.run(
            plan,
            dry_run=dry_run,
            budget=TeamBudget(
                max_model_calls=max_model_calls,
                max_cost_units=max_cost_units,
            ),
        )
    except ValueError as exc:
        print(f"Usage: {exc}", file=sys.stderr)
        return True
    event = "team_execution" if not dry_run else "team_execution_dry_run"
    agent.log.log(event, report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True


def _handle_approval_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "pending"
    items = _approval_inbox_for(agent, workspace).list(status=status)
    if not items:
        print(f"(no approvals: status={status})", file=sys.stderr)
        return True
    print(f"=== approval inbox ({len(items)}; status={status}) ===", file=sys.stderr)
    for item in items:
        print(
            f"  {item.id} [{item.status}] risk={item.risk} "
            f"operation={item.operation} summary={item.summary}",
            file=sys.stderr,
        )
        if item.reasons:
            print(f"    reasons={list(item.reasons)}", file=sys.stderr)
    return True


def _handle_approval_decision(
    rest: str,
    agent: AgentLoop,
    workspace: Path,
    *,
    decision: str,
) -> bool:
    item_id = rest.strip()
    if not item_id:
        print(f"Usage: :approval-{decision} <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    try:
        if decision == "approve":
            item = inbox.approve(item_id)
        elif decision == "deny":
            item = inbox.deny(item_id)
        else:
            raise ValueError(f"unknown approval decision: {decision}")
    except KeyError as exc:
        print(f"(approval {decision} failed: {exc})", file=sys.stderr)
        return True
    agent.log.log("approval_inbox_decision", item.to_dict())
    past = "approved" if decision == "approve" else "denied"
    print(f"(approval {past}: {item.id}; operation={item.operation})", file=sys.stderr)
    return True


def _handle_approval_abort(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    item_id = rest.strip()
    if not item_id:
        print("Usage: :approval-abort <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    try:
        item = inbox.abort(item_id)
    except KeyError as exc:
        print(f"(approval abort failed: {exc})", file=sys.stderr)
        return True
    agent.log.log("approval_inbox_decision", item.to_dict())
    print(f"(approval aborted: {item.id}; operation={item.operation})", file=sys.stderr)
    return True


def _payload_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _handle_approval_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    item_id = rest.strip()
    if not item_id:
        print("Usage: :approval-run <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    item = inbox.get(item_id)
    if item is None:
        print(f"(approval run failed: approval not found: {item_id})", file=sys.stderr)
        return True
    if item.status != "approved":
        print(
            f"(approval run refused: {item.id} status={item.status}; approve it first)",
            file=sys.stderr,
        )
        return True
    if item.operation != "autonomous_runtime.allow_effects":
        print(
            f"(approval run refused: unsupported operation={item.operation})",
            file=sys.stderr,
        )
        return True

    payload = item.payload
    try:
        config = AutonomousRuntimeConfig(
            goal=str(payload.get("goal") or "project health"),
            dry_run=False,
            effects_approved=True,
            limit=max(1, int(payload.get("limit", 5))),
            include_tests=_payload_bool(payload.get("include_tests"), default=True),
            learning_limit=max(1, int(payload.get("learning_limit", 5))),
        )
    except (TypeError, ValueError) as exc:
        print(f"(approval run failed: invalid payload: {exc})", file=sys.stderr)
        return True

    report = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=inbox,
    ).run(config)
    if report.status == "completed":
        executed = inbox.mark_executed(item.id)
        agent.log.log("approval_inbox_executed", executed.to_dict())
    print(report.user_summary(), file=sys.stderr)
    return True


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
        print(
            f"  {task.id} [{task.status}] priority={task.priority} "
            f"attempts={task.attempts}/{task.max_attempts} run_after={task.run_after} "
            f"goal={task.goal}",
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
            "  :ingest-web <topic> [flags]     search/fetch curated web library sources\n"
            "      flags: --sources wikis|books|science|docs|all|id,id  --limit N  --per-source N\n"
            "  :ingest-rss <url> [flags]       fetch RSS/Atom feed entries into Source Registry\n"
            "      flags: --limit N  --dry-run  --write-memory  --no-memory\n"
            "  :connectors [status] [--json]   list source connectors and rough costs\n"
            "  :connector-plan <goal> [flags]  recommend source connectors for a task\n"
            "      flags: --limit N  --json\n"
            "  :models [--json]                inspect model routes and registry\n"
            "  :model-registry-audit [--json] inspect selected vs available model candidates\n"
            "  :architecture-audit [--json]   inspect layers and multi-agent gaps\n"
            "  :operator-check [--json]       conversational project/status digest\n"
            "  :operator-budget [--json]      concise budget + model usage digest\n"
            "  :budget-config [--json]        inspect budget limit config and env overrides\n"
            "  :urgent-status [--json]        approvals + queue + scheduler urgency digest\n"
            "  :next-actions [--json]         architecture priorities + recommendations\n"
            "  :autonomy-readiness [--json]   whether autonomy is safe to run now\n"
            "  :coding-readiness [--json]     safe programming task readiness report\n"
            "  :operator-task ... :end        one safe multi-line operator task report\n"
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
            "  :auto-status                    inspect autonomous runtime inbox/status\n"
            "  :conflicts [--limit N|--json]   inspect source claim conflicts and suggestions\n"
            "  :budget-status                  inspect default autonomous runtime budgets\n"
            "  :budget-window-status [--json] inspect persistent hour/day budget windows\n"
            "  :state-store-drill [--json]    prove JSONL quarantine/recovery on an isolated file\n"
            "  :release-audit [--json]         inspect release artifact hygiene exclusions\n"
            "  :supply-chain-audit [--json]   inspect pinned deps and CI release gates\n"
            "  :approval-list [status|all]     list pending/approved/denied approval items\n"
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
            "  empty line                      exit",
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
            "If it crashed before synthesis, the full cycle is re-run from scratch."
        ),
    )
    args = parser.parse_args()

    # Must run BEFORE any non-ASCII input flows through stdin / out.
    _force_utf8_io()
    load_dotenv()

    workspace = Path(args.workspace).resolve()

    # §3.5 Resume: if --resume is given, look up the checkpoint file and
    # short-circuit before building the full agent stack when possible.
    if args.resume:
        from core.checkpoint import CheckpointLoader as _CPLoader
        _log_dir = workspace / "logs"
        _loader = _CPLoader(_log_dir)
        _ctx = _loader.load(args.resume)
        if _ctx is None:
            print(
                f"[resume] No usable checkpoint found for trace_id={args.resume!r}. "
                "Running fresh.",
                file=sys.stderr,
            )
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

        agent = build_agent(workspace, with_memory=False, approval_provider=approval_provider)
        if handle_conversational_operator_input(args.ask, agent, workspace):
            return 0
        answer = _run_agent_with_budget_guard(
            agent,
            user_question=args.ask,
            file_hint=args.file,
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
        "Commands: :memory  :smart-memory  :memory-consolidate  :learn  :auto-run  :work-session  :capability-request  :subagent-proposal  :operator-check  :operator-budget  :budget-config  :urgent-status  :next-actions  :autonomy-readiness  :coding-readiness  :operator-task  :conflicts  :budget-status  :budget-window-status  :state-store-drill  :release-audit  :supply-chain-audit  :model-usage  :team-plan  :team-run  :architecture-audit  :model-registry-audit  :approval-list  :approval-run  :task-add  :schedule-tick  :auto-status  :source-library  :source-registry  :source-review-plan  :implementation-plan  :patch-proposal-plan  :connectors  :connector-plan  :models  :ingest-web  :ingest-rss  :ingest-source  :ingest-project  :remember  :forget  :propose-repair  :repair  :help  :quit",
        file=sys.stderr,
    )
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
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
        if q.startswith(":") or q == "?":
            if handle_meta_command(q, agent, workspace):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
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
        )
        print("\n" + format_human_response(answer) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
