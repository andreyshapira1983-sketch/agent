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
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.budget_governor import BudgetGovernor, BudgetLimits
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
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_usage import ModelUsageLedger
from core.model_router import ModelRole, ModelRouter
from core.persistent_memory import PersistentMemoryStore
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
from core.task_queue import TaskQueueStore
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.rss_fetch import RssFetchTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool


DEFAULT_PERSISTENT_PATH = Path("data") / "persistent_memory.jsonl"
DEFAULT_SOURCE_REGISTRY_PATH = Path("data") / "source_registry.jsonl"
DEFAULT_RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"
DEFAULT_RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
DEFAULT_APPROVAL_INBOX_PATH = Path("data") / "approval_inbox.jsonl"
DEFAULT_MODEL_USAGE_PATH = Path("data") / "model_usage.jsonl"


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

    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=True)

    policy = PolicyGate(registry)
    model_usage_ledger = ModelUsageLedger.from_env(
        path=workspace / DEFAULT_MODEL_USAGE_PATH,
        logger=logger,
    )
    model_router = ModelRouter.from_env(usage_ledger=model_usage_ledger)
    llm = model_router.for_role(ModelRole.SYNTHESIZER)
    planner = LLMPlanner(
        llm=model_router.for_role(ModelRole.PLANNER),
        registry=registry,
    )
    memory = WorkingMemory() if with_memory else None

    persistent_store: PersistentMemoryStore | None = None
    source_registry_store: SourceRegistryStore | None = None
    if with_persistent:
        full_path = persistent_path or (workspace / DEFAULT_PERSISTENT_PATH)
        persistent_store = PersistentMemoryStore(full_path)
        source_registry_store = SourceRegistryStore(
            workspace / DEFAULT_SOURCE_REGISTRY_PATH
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
            "model_usage_path": str(workspace / DEFAULT_MODEL_USAGE_PATH),
            "knowledge_auto_write": _env_bool("AGENT_KNOWLEDGE_AUTO_WRITE", False),
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
        knowledge_auto_write=_env_bool("AGENT_KNOWLEDGE_AUTO_WRITE", False),
        approval_provider=approval_provider,
    )


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
    payload = {
        "routes": agent.model_router.routing_summary(),
        "registry": agent.model_router.registry_summary(),
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

    try:
        report = AutonomousRuntime(
            agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        ).run(
            AutonomousRuntimeConfig(
                goal=" ".join(goal_parts).strip() or "project health",
                dry_run=dry_run,
                limit=limit,
                include_tests=include_tests,
                learning_limit=learning_limit,
            )
        )
    except Exception as exc:
        print(f"(auto-run failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(report.user_summary(), file=sys.stderr)
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
    print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


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
    print("=== autonomous budget defaults ===", file=sys.stderr)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=sys.stderr)
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

    if head in {":learn", ":learn-project"}:
        return _handle_learn(rest.strip(), agent, workspace)

    if head == ":auto-run":
        return _handle_auto_run(rest.strip(), agent, workspace)

    if head == ":auto-status":
        return _handle_auto_status(agent, workspace)

    if head in {":conflicts", ":conflict-status"}:
        return _handle_conflicts(rest.strip(), agent, workspace)

    if head == ":budget-status":
        return _handle_budget_status(agent, workspace)

    if head in {":model-usage", ":usage-models"}:
        return _handle_model_usage(rest.strip(), agent)

    if head == ":approval-list":
        return _handle_approval_list(rest.strip(), agent, workspace)

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
            "  :clear                          wipe working memory only\n"
            "  :remember [tags] <text>         save to persistent memory (Write Policy gated)\n"
            "  :forget [id|all]                delete persistent record(s)\n"
            "  :ingest-source <path> [flags]   ingest one UTF-8 text/code file into Source Registry\n"
            "  :ingest-project [path] [flags]  ingest project text/code files (default limit 80)\n"
            "  :source-library [group|all]     list curated online source families\n"
            "  :ingest-web <topic> [flags]     search/fetch curated web library sources\n"
            "      flags: --sources wikis|books|science|docs|all|id,id  --limit N  --per-source N\n"
            "  :ingest-rss <url> [flags]       fetch RSS/Atom feed entries into Source Registry\n"
            "      flags: --limit N  --dry-run  --write-memory  --no-memory\n"
            "  :connectors [status] [--json]   list source connectors and rough costs\n"
            "  :connector-plan <goal> [flags]  recommend source connectors for a task\n"
            "      flags: --limit N  --json\n"
            "  :models [--json]                inspect model routes and registry\n"
            "  :model-usage [--json]           inspect model calls/tokens/cost units\n"
            "  :learn [goal] [flags]           plan sources, then ingest selected learning set\n"
            "  :learn-project [goal] [flags]   alias for :learn\n"
            "      flags: --dry-run  --write-memory  --no-memory  --limit N\n"
            "  :auto-run [goal] [flags]        bounded autonomous health pass\n"
            "      flags: --dry-run  --allow-effects  --limit N  --learning-limit N  --no-tests\n"
            "  :auto-status                    inspect autonomous runtime inbox/status\n"
            "  :conflicts [--limit N|--json]   inspect source claim conflicts and suggestions\n"
            "  :budget-status                  inspect default autonomous runtime budgets\n"
            "  :approval-list [status|all]     list pending/approved/denied approval items\n"
            "  :approval-approve <id>          mark an approval inbox item approved\n"
            "  :approval-deny <id>             mark an approval inbox item denied\n"
            "  :approval-run <id>              execute one approved whitelisted operation\n"
            "  :approval-abort <id>            mark an approval inbox item aborted\n"
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
            "      (no subcmd)      run expire, then dedupe, then backups\n"
            "  :rollback [plan_id]             apply latest compensation plan (or by id);\n"
            "                                  no arg = LIFO pop; 'list' = show registered plans\n"
            "  :repair <target> <proposal> [tests...] [--pattern PAT]\n"
            "                                  guarded self-repair: diff, approval, write, tests, rollback\n"
            "  :propose-repair <target> [tests...] [--pattern PAT] [--trace TRACE]\n"
            "                                  generate a RepairProposal without writing files\n"
            "  :quit | :exit                   exit\n"
            "  empty line                      exit",
            file=sys.stderr,
        )
        return True

    if head in {":quit", ":exit"}:
        raise SystemExit(0)

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
    args = parser.parse_args()

    # Must run BEFORE any non-ASCII input flows through stdin / out.
    _force_utf8_io()
    load_dotenv()

    workspace = Path(args.workspace).resolve()

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
        answer = agent.run(user_question=args.ask, file_hint=args.file)
        print("\n" + answer + "\n")
        return 0

    if args.auto_approve == "approve":
        approval_provider = AutoApprover(default="approve")
    elif args.auto_approve == "deny":
        approval_provider = AutoApprover(default="deny")
    else:
        approval_provider = CLIApprovalProvider()

    # Interactive — single agent with working + persistent memory + approval UX
    agent = build_agent(workspace, with_memory=True, approval_provider=approval_provider)

    print(
        f"Agent ready. file_hint={args.file or '-'}  memory=on  persistent=on  "
        f"approval={type(approval_provider).__name__}. "
        "Commands: :memory  :learn  :auto-run  :conflicts  :budget-status  :model-usage  :approval-list  :approval-run  :task-add  :schedule-tick  :auto-status  :source-library  :connectors  :connector-plan  :models  :ingest-web  :ingest-rss  :ingest-source  :ingest-project  :remember  :forget  :propose-repair  :repair  :help  :quit",
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
        if q.startswith(":") or q == "?":
            if handle_meta_command(q, agent, workspace):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
            continue
        answer = agent.run(user_question=q, file_hint=args.file)
        print("\n" + answer + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
