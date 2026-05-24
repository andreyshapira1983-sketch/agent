"""
runtime/agent_runtime.py — Glue all subsystems into one live `AgentRuntime`.

This is the only place that knows about every component at once:
Brain + memory + policy + audit + budget + ToolRegistry + SkillRegistry
+ JobStore + Portfolio + chat handler + channels.

Everything is wired with sensible defaults from `AgentConfig`. Components
that are not configured (e.g. no Telegram token) gracefully degrade to
`None` rather than crashing the runtime.

Typical usage:

    cfg, vault = load_config()
    rt = build_runtime(cfg, vault)
    LiveLoop(rt).run_forever()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.adapters.openai_adapter import OpenAIAdapter
from brain.audit import AuditLog
from brain.budget import BudgetController
from brain.core import Brain
from brain.memory.memory_manager import MemoryManager
from brain.metrics import MetricsService
from brain.planner import PlanCheckpointStore
from brain.policy import PolicyEngine
from brain.privacy import PIIRedactor
from brain.secrets import SecretsVault
from brain.state import IdempotencyStore, RecoveryManager, TaskStore
from brain.skills.job import JobStore
from brain.skills.portfolio import Portfolio
from brain.skills.registry import SkillRegistry
from brain.skills.workflow_runner import ToolResult as WorkflowToolResult
from brain.skills.workflow_runner import ToolRunner

from tools.builtins import (
    DocxReaderTool, DocxWriterTool, EmailTool, FileWriteTool,
    PptxWriterTool, XlsxWriterTool,
)
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry

from .chat import ChatHandler
from .config import AgentConfig
from .email_sender import EmailSender
from .telegram_sender import TelegramSender
from .welcome_book import WelcomeBook

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Runtime aggregate
# ════════════════════════════════════════════════════════════════════

@dataclass
class AgentRuntime:
    """Live, fully-wired agent. Hold this in a single variable."""

    config:     AgentConfig
    vault:      SecretsVault
    brain:      Brain
    memory:     MemoryManager
    redactor:   PIIRedactor
    policy:     PolicyEngine
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    skill_registry: SkillRegistry
    job_store:  JobStore
    audit:      AuditLog
    budget:     BudgetController
    portfolio:  Portfolio
    metrics:    MetricsService
    chat:       ChatHandler
    welcome_book:    WelcomeBook
    plan_checkpoints: PlanCheckpointStore
    task_store:       TaskStore
    idempotency:      IdempotencyStore
    recovery:         RecoveryManager

    # Optional channel pieces — None when not configured
    telegram_sender:  TelegramSender | None = None
    telegram_intake:  Any = None    # forward ref to TelegramIntakeChannel
    email_intake:     Any = None    # forward ref to EmailIntakeChannel
    email_sender:     EmailSender | None = None

    notes: list[str] = field(default_factory=list)

    def status(self) -> dict:
        return {
            "config":           self.config.summary(),
            "brain":            self.brain.status(),
            "memory":           self.memory.status(),
            "jobs":             len(self.job_store),
            "audit_entries":    len(self.audit),
            "audit_head":       self.audit.head(),
            "portfolio_size":   len(self.portfolio),
            "metrics":          self.metrics.snapshot().to_dict(),
            "welcomed_clients": len(self.welcome_book),
            "unfinished_plans": self.plan_checkpoints.unfinished_jobs(),
            "task_recovery":    self.recovery.status_report(),
            "idempotency_cache": self.idempotency.count(),
            "channels": {
                "telegram_in":  self.telegram_intake is not None,
                "telegram_out": self.telegram_sender is not None,
                "email_in":     self.email_intake is not None,
                "email_out":    self.email_sender is not None,
            },
            "notes":            self.notes,
        }

    # ────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Best-effort teardown — close SQLite handles before exit.

        Called by the live loop on shutdown. Idempotent: a second call
        after the first does nothing (failures swallowed because we're
        on the way out anyway).
        """
        for closeable in (
            self.audit, self.budget, self.job_store,
            self.portfolio, self.welcome_book, self.plan_checkpoints,
            self.task_store, self.idempotency,
        ):
            close = getattr(closeable, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # noqa: BLE001
                logger.exception("[runtime] close() failed on %r", closeable)


# ════════════════════════════════════════════════════════════════════
# Build
# ════════════════════════════════════════════════════════════════════

def build_runtime(
    config: AgentConfig,
    vault: SecretsVault,
    *,
    llm=None,
) -> AgentRuntime:
    """Assemble every subsystem the agent needs to run live.

    Args:
        config: From `load_config()`.
        vault:  Same vault load_config returned.
        llm:    Override the default LLM adapter (tests pass a fake).
    """
    data = config.data_dir
    data.mkdir(parents=True, exist_ok=True)

    redactor = PIIRedactor()
    policy = PolicyEngine()

    # ── persistent stores ───────────────────────────────────────
    memory = MemoryManager(
        episodic_db=str(data / "episodic.db"),
        semantic_dir=str(data / "semantic"),
    )
    job_store = JobStore(data / "jobs.db")
    audit = AuditLog(data / "audit.db")
    budget = BudgetController()
    portfolio = Portfolio(data / "portfolio.db")
    welcome_book = WelcomeBook(data / "welcome.db")
    plan_checkpoints = PlanCheckpointStore(data / "plans.db")
    task_store = TaskStore(data / "tasks.db")
    idempotency = IdempotencyStore(data / "idempotency.db")
    recovery = RecoveryManager(task_store, plan_checkpoints)

    # ── LLM ─────────────────────────────────────────────────────
    if llm is None:
        if not config.openai_ready:
            raise RuntimeError(
                "OPENAI_API_KEY missing — cannot build runtime. "
                "Add it to .env or pass llm= explicitly."
            )
        llm = OpenAIAdapter(model=config.openai_model, vault=vault)

    # ── tools ───────────────────────────────────────────────────
    tool_registry = ToolRegistry()
    _register_default_tools(
        tool_registry,
        vault=vault,
        workspace=config.workspace,
        dry_run_send=config.dry_run_send,
    )
    tool_executor = ToolExecutor(tool_registry, idempotency_store=idempotency)

    # ── skills ──────────────────────────────────────────────────
    skill_registry = SkillRegistry()
    _register_default_capabilities(skill_registry, tool_registry)
    if config.professions_dir.exists():
        skill_registry.load_directory(config.professions_dir)

    # WorkflowRunner needs a tool runner — adapter that delegates to executor.
    tool_runner = _ExecutorRunner(tool_executor)

    brain = Brain(
        llm=llm,
        memory=memory,
        redactor=redactor,
        policy=policy,
        autonomy_level=config.autonomy_level,
        skill_registry=skill_registry,
        tool_runner=tool_runner,
        job_store=job_store,
    )

    chat = ChatHandler(
        brain=brain,
        job_store=job_store,
        attachments_dir=config.attachments_dir,
        welcome_book=welcome_book,
    )

    metrics = MetricsService(
        audit=audit, budget=budget, job_store=job_store, portfolio=portfolio,
    )

    rt = AgentRuntime(
        config=config, vault=vault, brain=brain, memory=memory,
        redactor=redactor, policy=policy,
        tool_registry=tool_registry, tool_executor=tool_executor,
        skill_registry=skill_registry, job_store=job_store,
        audit=audit, budget=budget, portfolio=portfolio, metrics=metrics,
        chat=chat, welcome_book=welcome_book,
        plan_checkpoints=plan_checkpoints,
        task_store=task_store,
        idempotency=idempotency,
        recovery=recovery,
    )

    # ── channels (optional) ─────────────────────────────────────
    _wire_telegram(rt)
    _wire_email(rt)

    _warmup_checks(rt)
    rt.notes.append(_describe_readiness(rt))
    return rt


# ════════════════════════════════════════════════════════════════════
# Subsystem helpers
# ════════════════════════════════════════════════════════════════════

class _ExecutorRunner(ToolRunner):
    """Tiny adapter: WorkflowRunner.ToolRunner → ToolExecutor."""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    def run(self, tool_name: str, params: dict) -> WorkflowToolResult:
        result = self._executor.run(
            tool_name=tool_name,
            params=params,
            allow_destructive=True,  # workflow runner already passed policy gate
        )
        return WorkflowToolResult(
            success=result.success,
            output=result.output,
            error=result.error,
        )


def _register_default_tools(
    registry: ToolRegistry,
    *,
    vault: SecretsVault,
    workspace: Path,
    dry_run_send: bool,
) -> None:
    """Register the canonical tool set used by the shipped professions.

    Only tools whose dependencies the operator is likely to have are
    registered here. Optional tools (PDF, ODT, image readers) stay off
    until they're explicitly wanted.
    """
    registry.register(DocxReaderTool())
    registry.register(DocxWriterTool())
    registry.register(PptxWriterTool())
    registry.register(XlsxWriterTool())
    registry.register(FileWriteTool(workspace_root=workspace / "data" / "workspace"))
    registry.register(EmailTool(vault=vault, default_dry_run=dry_run_send))


def _register_default_capabilities(
    skills: SkillRegistry,
    tools: ToolRegistry,
) -> None:
    """Map capability ids referenced in profession YAMLs to actual tools.

    A capability is "the agent can do X" — usually proxied to one tool.
    Professions list the capability ids they need; SkillRegistry checks
    those against what's registered here.
    """
    pairs = [
        ("read_docx",         "docx_reader"),
        ("write_docx",        "docx_writer"),
        ("write_pptx",        "pptx_writer"),
        ("write_xlsx",        "xlsx_writer"),
        ("file_write",        "file_write"),
        ("send_email",        "email"),
        ("llm_text_rewrite",  ""),  # capability without a tool — pure LLM
    ]
    for cap_id, tool_name in pairs:
        if tool_name and not tools.has(tool_name):
            continue
        skills.register_tool_capability(cap_id, tool_name or cap_id)


def _wire_telegram(rt: AgentRuntime) -> None:
    if not rt.config.telegram_ready:
        rt.notes.append("telegram: skipped (no TELEGRAM_BOT_TOKEN)")
        return
    from channels.telegram_intake import TelegramIntakeChannel
    sender = TelegramSender(vault=rt.vault)
    intake = TelegramIntakeChannel(
        vault=rt.vault,
        attachments_dir=rt.config.attachments_dir / "telegram",
        long_poll_timeout=rt.config.telegram_poll_seconds,
    )
    rt.telegram_sender = sender
    rt.telegram_intake = intake


def _wire_email(rt: AgentRuntime) -> None:
    if rt.config.imap_ready:
        from channels.email_intake import EmailIntakeChannel
        rt.email_intake = EmailIntakeChannel(
            vault=rt.vault,
            attachments_dir=rt.config.attachments_dir / "email",
        )
    else:
        rt.notes.append("email-in: skipped (no IMAP credentials)")

    if rt.config.email_ready:
        rt.email_sender = EmailSender(
            vault=rt.vault, dry_run=rt.config.dry_run_send,
        )
    else:
        rt.notes.append("email-out: skipped (no SMTP credentials)")


def _warmup_checks(rt: AgentRuntime) -> None:
    """Run cheap "should we worry about anything?" checks at boot.

    1. Audit hash-chain integrity — if a row was tampered with, we want
       to know NOW, not the next time a regulator asks.
    2. Plan-checkpoint warmup — any job whose plan is still ACTIVE
       (typically: a crash mid-workflow) gets logged so the operator
       knows what was left unfinished.
    Neither check raises — both append to `rt.notes` and log.
    """
    try:
        report = rt.audit.verify_chain()
        if not report.ok:
            msg = (
                f"audit: CHAIN BROKEN at seq={report.first_bad_seq} "
                f"(expected={report.expected_hash} actual={report.actual_hash})"
            )
            logger.critical("[boot] %s", msg)
            rt.notes.append(msg)
        else:
            rt.notes.append(f"audit: chain ok ({report.total_entries} entries)")
    except Exception:  # noqa: BLE001
        logger.exception("[boot] audit.verify_chain failed")

    try:
        unfinished = rt.plan_checkpoints.unfinished_jobs()
    except Exception:  # noqa: BLE001
        logger.exception("[boot] plan_checkpoints.unfinished_jobs failed")
        unfinished = []
    if unfinished:
        # Keep the list short in the note; full list lives in /status.
        preview = ", ".join(unfinished[:3])
        more = f" (+{len(unfinished) - 3} more)" if len(unfinished) > 3 else ""
        msg = f"resume: {len(unfinished)} unfinished plan(s): {preview}{more}"
        logger.warning("[boot] %s", msg)
        rt.notes.append(msg)

    # ── Task recovery ────────────────────────────────────────────────
    try:
        recovered = rt.recovery.recover()
        if recovered:
            rt.notes.append(
                f"state: {len(recovered)} interrupted task(s) marked RECOVERING"
            )
    except Exception:  # noqa: BLE001
        logger.exception("[boot] recovery.recover() failed")


def _describe_readiness(rt: AgentRuntime) -> str:
    ready = []
    if rt.config.openai_ready:    ready.append("openai")
    if rt.config.telegram_ready:  ready.append("telegram")
    if rt.email_sender is not None: ready.append("email-out")
    if rt.email_intake is not None: ready.append("email-in")
    return "ready: " + (", ".join(ready) if ready else "no live channels")
