"""Pure command metadata for the operator (REPL/one-shot) command surface.

Phase 1 of the ``main.py`` extraction: **data only**. Nothing in the running
agent reads this module yet -- ``handle_meta_command`` still owns dispatch, and
``:help`` / the startup banner are still their own hand-written strings. Phase 2
is what switches those consumers over to this table.

Why it exists: the same command surface is currently spelled out in four
hand-maintained places (the dispatch chain in ``main.py``, the ``:help`` page,
the startup banner, and ``docs/COMMANDS_MAP.md``) and they already disagree --
see ``docs/refactor/CLI_BASELINE.md`` section 3.1.

Purity contract: this module imports **nothing** from ``core``, ``app``,
``cli.commands_*`` or any runtime module, and holds no behaviour. It must stay
importable with zero side effects. ``tests/test_command_registry.py`` enforces
both that and the agreement with the live code.

Field meanings (all recorded from the code at commit 72fc7a8):

- ``canonical`` -- the primary ``:token``. Chosen as the first token of the
  dispatch branch, which at this commit always matches the first token listed
  in ``:help`` and in ``docs/COMMANDS_MAP.md``.
- ``aliases`` -- the other tokens the same dispatch branch accepts.
- ``description`` -- taken from the live ``:help`` line for the canonical token,
  falling back to the ``COMMANDS_MAP`` row when ``:help`` omits it.
- ``usage`` -- the argument sketch documented for the command (``''`` if none).
- ``category`` -- the ``COMMANDS_MAP`` section the command is documented under.
- ``in_help`` -- whether the live ``:help`` page currently lists it.
- ``in_startup_summary`` -- whether the REPL startup banner currently lists it.
- ``modes`` -- where the token is accepted: every command works both one-shot
  (``--ask ':cmd'``) and in the REPL.
- ``phase`` -- ``'pre_dotenv'`` for the two commands that, **in one-shot only**,
  short-circuit before ``load_dotenv()`` and before any agent is built;
  ``'post_agent'`` for everything else. In the REPL both phases behave the same.
- ``handler_key`` -- a stable slug for the dispatch branch. Phase 3 maps these
  to the real handlers; nothing resolves them today.

Not modelled here on purpose (see CLI_BASELINE.md sections 3.1-3.2):

- REPL block/control tokens ``:task-begin``/``:task-end``/``:task-abort``/``:end``
  -- intercepted by the REPL loop, never dispatched through the head chain;
- the bare ``?`` help alias -- it lives outside the ``:token`` namespace;
- natural-language operator intents, four of which target handlers that have no
  ``:command`` equivalent at all.
"""
from __future__ import annotations

from dataclasses import dataclass


ONE_SHOT = "one_shot"
REPL = "repl"
BOTH_MODES = (ONE_SHOT, REPL)

PHASE_PRE_DOTENV = "pre_dotenv"
PHASE_POST_AGENT = "post_agent"


@dataclass(frozen=True)
class CommandSpec:
    """One dispatch branch of the operator command surface."""

    canonical: str
    description: str
    category: str
    handler_key: str
    aliases: tuple[str, ...] = ()
    usage: str = ""
    in_help: bool = True
    in_startup_summary: bool = False
    modes: tuple[str, ...] = BOTH_MODES
    phase: str = PHASE_POST_AGENT

    @property
    def tokens(self) -> tuple[str, ...]:
        """Every token this branch accepts, canonical first."""
        return (self.canonical, *self.aliases)


COMMANDS: tuple[CommandSpec, ...] = (
    # ── Memory ──────────────────────────────────────────────────────
    CommandSpec(
        canonical=":mem",
        description="inspect working + persistent memory",
        category="Memory",
        handler_key="mem",
        aliases=(":memory",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":smart-memory",
        description="inspect episodic/procedural/consolidation memory",
        category="Memory",
        handler_key="smart_memory",
        aliases=(":memory-status",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":memory-consolidate",
        description="link episodes to reusable procedures now",
        category="Memory",
        handler_key="memory_consolidate",
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":audit",
        description="read-only audit mode: freeze all durable memory writes",
        category="Memory",
        handler_key="audit",
        usage="[on|off|status]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":clear",
        description="wipe working memory only",
        category="Memory",
        handler_key="clear",
        aliases=(":reset",),
    ),
    CommandSpec(
        canonical=":remember",
        description="save to persistent memory (Write Policy gated)",
        category="Memory",
        handler_key="remember",
        usage="[tags] <text>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":forget",
        description="delete persistent record(s)",
        category="Memory",
        handler_key="forget",
        usage="[id|all]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":hygiene",
        description="memory hygiene; subcmd:",
        category="Memory",
        handler_key="hygiene",
        usage="[subcmd] [--dry-run]",
    ),
    # ── Sources, ingestion & knowledge ──────────────────────────────
    CommandSpec(
        canonical=":ingest-source",
        description="ingest one UTF-8 text/code file into Source Registry",
        category="Sources, ingestion & knowledge",
        handler_key="ingest_source",
        usage="<path>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ingest-project",
        description="ingest project text/code files (default limit 80)",
        category="Sources, ingestion & knowledge",
        handler_key="ingest_project",
        usage="[path]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":source-library",
        description="list curated online source families",
        category="Sources, ingestion & knowledge",
        handler_key="source_library",
        usage="[group|all]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":source-registry",
        description="list ingested sources and claim counts",
        category="Sources, ingestion & knowledge",
        handler_key="source_registry",
        aliases=(":source-status",),
        usage="[flags]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":source-review-plan",
        description="compare requested files/sources against Source Registry",
        category="Sources, ingestion & knowledge",
        handler_key="source_review_plan",
        usage="<goal>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":implementation-plan",
        description="local source-backed implementation plan",
        category="Sources, ingestion & knowledge",
        handler_key="implementation_plan",
        usage="<goal>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":patch-proposal-plan",
        description="local read-only patch proposal plan",
        category="Sources, ingestion & knowledge",
        handler_key="patch_proposal_plan",
        aliases=(":patch-plan",),
        usage="<goal>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ingest-web",
        description="search/fetch curated web library sources",
        category="Sources, ingestion & knowledge",
        handler_key="ingest_web",
        usage="<topic> [--sources …]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ingest-rss",
        description="fetch RSS/Atom feed entries into Source Registry",
        category="Sources, ingestion & knowledge",
        handler_key="ingest_rss",
        usage="<feed_url> [--limit N]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":connectors",
        description="list source connectors and rough costs",
        category="Sources, ingestion & knowledge",
        handler_key="connectors",
        usage="[all|wired|partial|planned]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":connector-plan",
        description="recommend source connectors for a task",
        category="Sources, ingestion & knowledge",
        handler_key="connector_plan",
        usage="<goal>",
        in_startup_summary=True,
    ),
    # ── Self-improvement (self-build / self-repair) — human-gated ───
    CommandSpec(
        canonical=":self-build-propose",
        description="propose a self-build patch or NO_PATCH",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_build_propose",
        usage="<…>",
        in_startup_summary=True,
        phase=PHASE_PRE_DOTENV,
    ),
    CommandSpec(
        canonical=":self-build-supervisor",
        description="read-only supervisor: wait/stop/propose one candidate",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_build_supervisor",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-issue-verify",
        description="Verify a self-improvement issue.",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_issue_verify",
        usage="<fingerprint>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-apply-run",
        description="run one approved low-risk self-apply proposal",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_apply_run",
        usage="<inbox_id>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-build-produce",
        description="produce one low-risk self-apply proposal into the inbox",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_build_produce",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-split",
        description="plan one deterministic incremental split step for an oversized module",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_split",
        usage="<path.py>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-task-propose",
        description="propose one coding task + failing test for approval (Stage A)",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_task_propose",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":self-task-build",
        description="implement an approved coding task so its frozen test passes (Stage B)",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="self_task_build",
        usage="<approval_id>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":value-review",
        description="record a human value verdict for an applied proposal",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="value_review",
        usage="<item_id> <verdict> [note]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":value-review-list",
        description="list applied self-build proposals and their value verdicts",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="value_review_list",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":rollback",
        description="apply latest compensation plan (or by id);",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="rollback",
        usage="[plan_id]",
    ),
    CommandSpec(
        canonical=":repair",
        description="Apply/inspect a self-repair.",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="repair",
        usage="<target> [proposal] [tests] [--pattern]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":propose-repair",
        description="Generate a repair proposal.",
        category="Self-improvement (self-build / self-repair) — human-gated",
        handler_key="propose_repair",
        usage="<target> [tests] [--pattern] [--trace]",
        in_startup_summary=True,
    ),
    # ── Models & budget ─────────────────────────────────────────────
    CommandSpec(
        canonical=":models",
        description="inspect model routes and registry",
        category="Models & budget",
        handler_key="models",
        aliases=(":model-routes",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":model-registry-audit",
        description="Audit model registry / active routes.",
        category="Models & budget",
        handler_key="model_registry_audit",
        aliases=(":model-audit",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":refresh-models",
        description="query providers, persist model catalog",
        category="Models & budget",
        handler_key="refresh_models",
        aliases=(":model-catalog-refresh", ":model-refresh",),
    ),
    CommandSpec(
        canonical=":model-discovery-audit",
        description="Read-only model discovery audit.",
        category="Models & budget",
        handler_key="model_discovery_audit",
        aliases=(":discovery-audit",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":provider-catalog-refresh",
        description="Dry-run diff vs live provider models.",
        category="Models & budget",
        handler_key="provider_catalog_refresh",
        usage="--dry-run",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":operator-budget",
        description="concise budget + model usage digest",
        category="Models & budget",
        handler_key="operator_budget",
        aliases=(":budget-digest",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":budget-config",
        description="inspect budget limit config and env overrides",
        category="Models & budget",
        handler_key="budget_config",
        aliases=(":budget-limits",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":budget-status",
        description="inspect default autonomous runtime budgets",
        category="Models & budget",
        handler_key="budget_status",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":budget-window-status",
        description="Budget windows.",
        category="Models & budget",
        handler_key="budget_window_status",
        aliases=(":budget-windows", ":budget-ledger",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":budget-kill-switch",
        description="Inspect / clear the budget kill-switch.",
        category="Models & budget",
        handler_key="budget_kill_switch",
        aliases=(":budget-killswitch", ":kill-switch",),
        usage="[--clear]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":model-usage",
        description="inspect model calls/tokens/cost units",
        category="Models & budget",
        handler_key="model_usage",
        aliases=(":usage-models",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    # ── Operator status, audits & readiness ─────────────────────────
    CommandSpec(
        canonical=":architecture-audit",
        description="inspect layers and multi-agent gaps",
        category="Operator status, audits & readiness",
        handler_key="architecture_audit",
        aliases=(":arch-audit", ":roadmap-audit",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":operator-check",
        description="conversational project/status digest",
        category="Operator status, audits & readiness",
        handler_key="operator_check",
        aliases=(":project-check", ":project-status",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":urgent-status",
        description="approvals + queue + scheduler urgency digest",
        category="Operator status, audits & readiness",
        handler_key="urgent_status",
        aliases=(":operator-urgent",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":next-actions",
        description="architecture priorities + recommendations",
        category="Operator status, audits & readiness",
        handler_key="next_actions",
        aliases=(":operator-next",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":autonomy-readiness",
        description="whether autonomy is safe to run now",
        category="Operator status, audits & readiness",
        handler_key="autonomy_readiness",
        aliases=(":operator-readiness",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":dry-health-pass",
        description="local read-only autonomous health panel",
        category="Operator status, audits & readiness",
        handler_key="dry_health_pass",
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":coding-readiness",
        description="safe programming task readiness report",
        category="Operator status, audits & readiness",
        handler_key="coding_readiness",
        aliases=(":programming-readiness",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":operator-task",
        description="one safe multi-line operator task report",
        category="Operator status, audits & readiness",
        handler_key="operator_task",
        usage="<…>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":learn",
        description="plan sources, then ingest selected learning set",
        category="Operator status, audits & readiness",
        handler_key="learn",
        aliases=(":learn-project",),
        usage="[goal] [flags]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":conflicts",
        description="inspect source claim conflicts and suggestions",
        category="Operator status, audits & readiness",
        handler_key="conflicts",
        aliases=(":conflict-status",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":state-store-drill",
        description="prove JSONL quarantine/recovery on an isolated file",
        category="Operator status, audits & readiness",
        handler_key="state_store_drill",
        aliases=(":state-drill", ":state-recovery-drill",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":release-audit",
        description="inspect release artifact hygiene exclusions",
        category="Operator status, audits & readiness",
        handler_key="release_audit",
        aliases=(":release-hygiene",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":supply-chain-audit",
        description="inspect pinned deps and CI release gates",
        category="Operator status, audits & readiness",
        handler_key="supply_chain_audit",
        aliases=(":supply-audit", ":ci-audit",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":best-next-action",
        description="choose the single most important next action (advisory)",
        category="Operator status, audits & readiness",
        handler_key="best_next_action",
        aliases=(":next-action", ":bna",),
        usage="[--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":assumptions",
        description="show the last 20 logged planning assumptions",
        category="Operator status, audits & readiness",
        handler_key="assumptions",
        aliases=(":assumption-log",),
        usage="[--json]",
    ),
    # ── Autonomous runtime, tasks & scheduling ──────────────────────
    CommandSpec(
        canonical=":auto-run",
        description="bounded autonomous health pass",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="auto_run",
        usage="[goal] [flags]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":work-session",
        description="bounded multi-cycle session with time budget",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="work_session",
        aliases=(":work-sess",),
        usage="[goal] [flags]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":campaign-start",
        description="budgeted autonomous campaign with per-cycle ledger",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="campaign_start",
        aliases=(":campaign",),
        usage="[goal] [flags]",
    ),
    CommandSpec(
        canonical=":campaign-status",
        description="read the campaign ledger digest (no budget spent)",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="campaign_status",
        aliases=(":campaign-ledger",),
    ),
    CommandSpec(
        canonical=":capability-request",
        description="Propose a capability request.",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="capability_request",
        aliases=(":capability-proposal",),
        usage="<goal> [--submit] [--json]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":auto-status",
        description="inspect autonomous runtime inbox/status",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="auto_status",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":queue-status",
        description="inspect runtime task queue summary",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="queue_status",
    ),
    CommandSpec(
        canonical=":scheduler-status",
        description="inspect scheduler summary",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="scheduler_status",
    ),
    CommandSpec(
        canonical=":task-add",
        description="enqueue persistent autonomous task",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="task_add",
        usage="[goal] [flags]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":task-list",
        description="list runtime tasks",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="task_list",
        usage="[status|all]",
    ),
    CommandSpec(
        canonical=":task-run",
        description="run due pending runtime task(s)",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="task_run",
    ),
    CommandSpec(
        canonical=":task-cancel",
        description="cancel one queued task",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="task_cancel",
        usage="<task_id>",
    ),
    CommandSpec(
        canonical=":task-unblock",
        description="return an approval-blocked task to pending",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="task_unblock",
        usage="<task_id>",
    ),
    CommandSpec(
        canonical=":schedule-add",
        description="create recurring scheduler entry",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="schedule_add",
        usage="<min> <goal>",
    ),
    CommandSpec(
        canonical=":schedule-list",
        description="list schedules",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="schedule_list",
        usage="[status|all]",
    ),
    CommandSpec(
        canonical=":schedule-disable",
        description="disable a runtime schedule without running it",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="schedule_disable",
        usage="<id>",
        in_startup_summary=True,
        phase=PHASE_PRE_DOTENV,
    ),
    CommandSpec(
        canonical=":schedule-tick",
        description="enqueue due schedule tasks, optionally run them",
        category="Autonomous runtime, tasks & scheduling",
        handler_key="schedule_tick",
        usage="[--run]",
        in_startup_summary=True,
    ),
    # ── Multi-agent / subagents (bounded — see governance doc) ──────
    CommandSpec(
        canonical=":team-plan",
        description="dry-run bounded subagent contracts",
        category="Multi-agent / subagents (bounded — see governance doc)",
        handler_key="team_plan",
        aliases=(":agent-team", ":subagents",),
        usage="<goal>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":subagent-proposal",
        description="autonomous subagent initiative proposal",
        category="Multi-agent / subagents (bounded — see governance doc)",
        handler_key="subagent_proposal",
        aliases=(":propose-subagent",),
        usage="<…>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":team-run",
        description="dry-run execution walk over subagent contracts",
        category="Multi-agent / subagents (bounded — see governance doc)",
        handler_key="team_run",
        aliases=(":team-execute", ":subagents-run",),
        usage="<goal>",
        in_startup_summary=True,
    ),
    # ── Approvals & alerts ──────────────────────────────────────────
    CommandSpec(
        canonical=":approval-list",
        description="list pending/approved/denied approval items",
        category="Approvals & alerts",
        handler_key="approval_list",
        usage="[state]",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":approval-triage",
        description="read-only triage: clusters/duplicates/stale + advice",
        category="Approvals & alerts",
        handler_key="approval_triage",
        aliases=(":triage",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ack",
        description="acknowledge an advisory alert so it stops dominating BNA",
        category="Approvals & alerts",
        handler_key="ack",
        aliases=(":acknowledge",),
        usage="<action>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ack-list",
        description="list active acknowledgements",
        category="Approvals & alerts",
        handler_key="ack_list",
        aliases=(":acks",),
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":ack-clear",
        description="restore an acknowledged alert to the top-pick race",
        category="Approvals & alerts",
        handler_key="ack_clear",
        aliases=(":unack",),
        usage="<action>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":inbox",
        description="shortcut: list pending approvals",
        category="Approvals & alerts",
        handler_key="inbox",
    ),
    CommandSpec(
        canonical=":approve",
        description="shortcut: :approval-approve",
        category="Approvals & alerts",
        handler_key="approve",
        usage="<id>",
    ),
    CommandSpec(
        canonical=":deny",
        description="shortcut: :approval-deny",
        category="Approvals & alerts",
        handler_key="deny",
        usage="<id>",
    ),
    CommandSpec(
        canonical=":approval-approve",
        description="mark an approval inbox item approved",
        category="Approvals & alerts",
        handler_key="approval_approve",
        usage="<id>",
    ),
    CommandSpec(
        canonical=":approval-deny",
        description="mark an approval inbox item denied",
        category="Approvals & alerts",
        handler_key="approval_deny",
        usage="<id>",
    ),
    CommandSpec(
        canonical=":approval-run",
        description="execute one approved whitelisted operation",
        category="Approvals & alerts",
        handler_key="approval_run",
        usage="<id>",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":approval-abort",
        description="mark an approval inbox item aborted",
        category="Approvals & alerts",
        handler_key="approval_abort",
        usage="<id>",
    ),
    # ── Help ────────────────────────────────────────────────────────
    CommandSpec(
        canonical=":help",
        description="show this command help",
        category="Help",
        handler_key="help",
        in_startup_summary=True,
    ),
    CommandSpec(
        canonical=":quit",
        description="exit",
        category="Help",
        handler_key="quit",
        aliases=(":exit",),
        in_startup_summary=True,
    ),
)


BY_TOKEN: dict[str, CommandSpec] = {
    token: spec for spec in COMMANDS for token in spec.tokens
}
"""Every accepted token (canonical and alias) mapped to its command."""


def all_tokens() -> frozenset[str]:
    """Every ``:token`` the dispatch chain accepts."""
    return frozenset(BY_TOKEN)


def canonical_tokens() -> tuple[str, ...]:
    """The canonical token of every command, in registry order."""
    return tuple(spec.canonical for spec in COMMANDS)


def lookup(token: str) -> CommandSpec | None:
    """Resolve a token (canonical or alias) to its command, or ``None``."""
    return BY_TOKEN.get(token.strip().lower())


def in_category(category: str) -> tuple[CommandSpec, ...]:
    """Every command documented under ``category``, in registry order."""
    return tuple(spec for spec in COMMANDS if spec.category == category)

