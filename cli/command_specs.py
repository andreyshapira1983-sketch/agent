"""Command metadata primitives and the first half of the command table.

This module holds the ``CommandSpec`` dataclass, the shared mode/phase
constants, and the command specs for the *Memory*, *Sources, ingestion &
knowledge*, *Self-improvement* and *Models & budget* categories.

Its contract: no runtime-layer imports (``core``/``app``/...) and no side
effects at import time — declarations only (a frozen dataclass and specs)
and is combined with :mod:`cli.command_specs_ops` by
:mod:`cli.command_registry`, which remains the single public entry point --
see that module's docstring for the full purity contract and field
meanings.
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


COMMANDS_CORE: tuple[CommandSpec, ...] = (
    # ── Memory ─────────────────────────────────────────────
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
    # ── Sources, ingestion & knowledge ───────────────────────
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
    # ── Models & budget ────────────────────────────────────
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
)
