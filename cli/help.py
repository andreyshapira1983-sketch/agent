"""Rendering of the operator help page and the REPL startup command summary.

Phase 2 of the ``main.py`` extraction. Both texts used to be hand-written string
literals inside ``main.py``; they now live here, and every command description
that can be is pulled from :mod:`cli.command_registry` instead of being spelled
out a second time.

Why some lines are still verbatim. The page is not a uniform table: 24 lines are
``flags:`` continuations belonging to the command above them, nine command lines
put their description on a continuation line or separate it with a single space
instead of the usual two, two lines document things that are not dispatchable
commands at all (``:task-begin``, and ``:learn-project`` which is an alias with
its own line), and seven are prose. Those are carried as ``Raw`` entries so the
rendered page stays **byte-identical** to what operators see today; see
``docs/refactor/CLI_BASELINE.md`` section 3.1 for the divergence record.

``tests/test_help_render.py`` holds the contract: the rendered page must equal a
frozen fixture captured from the pre-extraction code, and every command in the
registry must appear somewhere in the layout -- so a command cannot be added to
the registry and silently stay out of ``:help``.
"""
from __future__ import annotations

from cli.command_registry import lookup

# ── layout entry kinds ───────────────────────────────────────────────────────
#
# ("cmd", canonical, left, column)
#     Render as: two spaces, ``left`` (the command and any aliases/usage shown
#     on that line), padding out to ``column``, then the command's description
#     taken from the registry.
# ("raw", text)      -- emitted verbatim.
# ("blank",)         -- an empty line.

HELP_LAYOUT: tuple[tuple, ...] = (
    ("raw", "Commands:"),
    ("cmd", ":mem", ":mem | :memory", 34),
    ("cmd", ":smart-memory", ":smart-memory [--json]", 34),
    ("cmd", ":memory-consolidate", ":memory-consolidate [--json]", 34),
    ("cmd", ":audit", ":audit [on|off|status]", 34),
    ("cmd", ":clear", ":clear", 34),
    ("cmd", ":remember", ":remember [tags] <text>", 34),
    ("cmd", ":forget", ":forget [id|all]", 34),
    ("cmd", ":ingest-source", ":ingest-source <path> [flags]", 34),
    ("cmd", ":ingest-project", ":ingest-project [path] [flags]", 34),
    ("cmd", ":source-library", ":source-library [group|all]", 34),
    ("cmd", ":source-registry", ":source-registry [flags]", 34),
    ("raw", "      flags: --claims  --limit N  --json"),
    ("cmd", ":source-review-plan", ":source-review-plan <goal>", 34),
    ("raw", "      flags: --limit N  --json"),
    ("cmd", ":implementation-plan", ":implementation-plan <goal>", 34),
    ("raw", "      flags: --limit N  --json"),
    ("cmd", ":patch-proposal-plan", ":patch-proposal-plan <goal>", 34),
    ("raw", "      flags: --limit N  --json"),
    ("cmd", ":self-build-propose", ":self-build-propose", 34),
    ("cmd", ":self-build-supervisor", ":self-build-supervisor", 34),
    ("cmd", ":ingest-web", ":ingest-web <topic> [flags]", 34),
    ("raw", "      flags: --sources wikis|books|science|docs|all|id,id  --limit N  --per-source N"),
    ("cmd", ":ingest-rss", ":ingest-rss <url> [flags]", 34),
    ("raw", "      flags: --limit N  --dry-run  --write-memory  --no-memory"),
    ("cmd", ":connectors", ":connectors [status] [--json]", 34),
    ("cmd", ":connector-plan", ":connector-plan <goal> [flags]", 34),
    ("raw", "      flags: --limit N  --json"),
    ("cmd", ":models", ":models [--json]", 34),
    ("cmd", ":refresh-models", ":refresh-models", 34),
    ("raw", "  :model-registry-audit [--json] inspect selected vs available model candidates"),
    ("raw", "  :model-discovery-audit [--json] local (no-network) provider discovery readiness"),
    ("raw", "  :provider-catalog-refresh --dry-run [--anthropic] [--openai] [--json]"),
    ("raw", "                                 dry-run live model discovery + catalog diff (no write)"),
    ("cmd", ":architecture-audit", ":architecture-audit [--json]", 33),
    ("cmd", ":operator-check", ":operator-check [--json]", 33),
    ("cmd", ":operator-budget", ":operator-budget [--json]", 33),
    ("cmd", ":budget-config", ":budget-config [--json]", 33),
    ("cmd", ":urgent-status", ":urgent-status [--json]", 33),
    ("cmd", ":next-actions", ":next-actions [--json]", 33),
    ("cmd", ":autonomy-readiness", ":autonomy-readiness [--json]", 33),
    ("cmd", ":dry-health-pass", ":dry-health-pass [--json]", 33),
    ("cmd", ":coding-readiness", ":coding-readiness [--json]", 33),
    ("cmd", ":operator-task", ":operator-task ... :end", 33),
    ("raw", "  :task-begin ... :task-end       buffer a complex instruction (bypasses keyword router; :task-abort discards)"),
    ("cmd", ":model-usage", ":model-usage [--json]", 34),
    ("cmd", ":team-plan", ":team-plan <goal> [--json]", 34),
    ("cmd", ":team-run", ":team-run <goal> [--json]", 34),
    ("raw", "  :capability-request <goal> [--submit] [--json]"),
    ("raw", "                                    propose missing connector/capability boundaries"),
    ("cmd", ":subagent-proposal", ":subagent-proposal <goal> [--submit]", 40),
    ("cmd", ":learn", ":learn [goal] [flags]", 34),
    ("raw", "  :learn-project [goal] [flags]   alias for :learn"),
    ("raw", "      flags: --dry-run  --write-memory  --no-memory  --limit N"),
    ("cmd", ":auto-run", ":auto-run [goal] [flags]", 34),
    ("raw", "      flags: --dry-run  --allow-effects  --limit N  --learning-limit N  --no-tests"),
    ("cmd", ":work-session", ":work-session [goal] [flags]", 34),
    ("raw", "      flags: --dry-run  --allow-effects  --minutes N  --max-cycles N  --report-every N"),
    ("cmd", ":campaign-start", ":campaign-start [goal] [flags]", 34),
    ("raw", "      flags: --dry-run  --allow-effects  --cycles N  --max-llm-calls N  --max-cost-units N  --max-idle N"),
    ("cmd", ":campaign-status", ":campaign-status [--recent N]", 34),
    ("cmd", ":auto-status", ":auto-status", 34),
    ("cmd", ":conflicts", ":conflicts [--limit N|--json]", 34),
    ("cmd", ":budget-status", ":budget-status", 34),
    ("raw", "  :budget-window-status [--json] inspect persistent hour/day budget windows"),
    ("raw", "  :budget-kill-switch [--json] [--clear] inspect/reset autonomous day-budget kill-switch"),
    ("cmd", ":state-store-drill", ":state-store-drill [--json]", 33),
    ("cmd", ":release-audit", ":release-audit [--json]", 34),
    ("cmd", ":supply-chain-audit", ":supply-chain-audit [--json]", 33),
    ("cmd", ":approval-list", ":approval-list [status|all]", 34),
    ("cmd", ":approval-triage", ":approval-triage", 34),
    ("cmd", ":best-next-action", ":best-next-action [--json]", 34),
    ("raw", "  :self-issue-verify <fingerprint> run the issue's fixed targeted verifier and resolve on green"),
    ("cmd", ":ack", ":ack <action> [--ttl H] [why]", 34),
    ("cmd", ":ack-list", ":ack-list", 34),
    ("cmd", ":ack-clear", ":ack-clear <action>", 34),
    ("cmd", ":approval-approve", ":approval-approve <id>", 34),
    ("cmd", ":approval-deny", ":approval-deny <id>", 34),
    ("cmd", ":approval-run", ":approval-run <id>", 34),
    ("cmd", ":self-apply-run", ":self-apply-run <id>", 34),
    ("cmd", ":self-build-produce", ":self-build-produce", 34),
    ("cmd", ":self-split", ":self-split <path.py>", 34),
    ("cmd", ":self-task-propose", ":self-task-propose", 34),
    ("cmd", ":self-task-build", ":self-task-build <id>", 34),
    ("cmd", ":value-review", ":value-review <id> <verdict> [note]", 39),
    ("cmd", ":value-review-list", ":value-review-list", 34),
    ("cmd", ":approval-abort", ":approval-abort <id>", 34),
    ("cmd", ":inbox", ":inbox", 34),
    ("cmd", ":approve", ":approve <id>", 34),
    ("cmd", ":deny", ":deny <id>", 34),
    ("cmd", ":queue-status", ":queue-status", 34),
    ("cmd", ":scheduler-status", ":scheduler-status", 34),
    ("cmd", ":task-add", ":task-add [goal] [flags]", 34),
    ("cmd", ":task-list", ":task-list [status|all]", 34),
    ("cmd", ":task-run", ":task-run [--limit N]", 34),
    ("cmd", ":task-cancel", ":task-cancel <task_id>", 34),
    ("cmd", ":task-unblock", ":task-unblock <task_id>", 34),
    ("cmd", ":schedule-add", ":schedule-add <min> <goal>", 34),
    ("raw", "      flags: --name NAME  --no-tests  --limit N  --learning-limit N"),
    ("cmd", ":schedule-list", ":schedule-list [status|all]", 34),
    ("cmd", ":schedule-disable", ":schedule-disable <id>", 34),
    ("cmd", ":schedule-tick", ":schedule-tick [--run]", 34),
    ("cmd", ":hygiene", ":hygiene [subcmd] [--dry-run]", 34),
    ("raw", "      backups    delete old .bak.<ts> files (keep last 3, >14d old)"),
    ("raw", "      expire     drop persistent records past their TTL"),
    ("raw", "      dedupe     collapse near-duplicate persistent records"),
    ("raw", "      episodic   prune old low-quality episodes (retrieval distractors)"),
    ("raw", "      summarise <tag>  merge records sharing <tag> via LLM"),
    ("raw", "      archive [--threshold=N] [--min-age=N]  move low-value records to archive"),
    ("raw", "      (no subcmd)      run expire, dedupe, episodic, then backups"),
    ("cmd", ":rollback", ":rollback [plan_id]", 34),
    ("raw", "                                  no arg = LIFO pop; 'list' = show registered plans"),
    ("raw", "  :repair <target> <proposal> [tests...] [--pattern PAT]"),
    ("raw", "                                  guarded self-repair: diff, approval, write, tests, rollback"),
    ("raw", "  :propose-repair <target> [tests...] [--pattern PAT] [--trace TRACE]"),
    ("raw", "                                  generate a RepairProposal without writing files"),
    ("cmd", ":assumptions", ":assumptions [--json]", 34),
    ("cmd", ":help", ":help | ?", 34),
    ("cmd", ":quit", ":quit | :exit", 34),
    ("raw", "  empty line                      ignored (use :quit or Ctrl+C to exit)"),
    ("blank",),
    ("raw", "Conversational shortcuts:"),
    ("raw", "  Check the project and tell me what needs attention"),
    ("raw", "  Show which models are in use"),
    ("raw", "  How many tokens were spent and what is the budget"),
    ("raw", "  Is there anything urgent"),
    ("raw", "  What should we do next"),
    ("raw", "  Is autonomy ready to run"),
)

"""The startup summary is a flat list of tokens, two spaces apart, in its own
historical order -- deliberately a subset of the help page (69 dispatched
commands are absent from it by design).
"""
BANNER_PREFIX = "Commands: "
BANNER_SEPARATOR = "  "
BANNER_TOKENS: tuple[str, ...] = (
    ":memory",
    ":smart-memory",
    ":memory-consolidate",
    ":audit",
    ":learn",
    ":auto-run",
    ":work-session",
    ":capability-request",
    ":subagent-proposal",
    ":operator-check",
    ":operator-budget",
    ":budget-config",
    ":urgent-status",
    ":next-actions",
    ":autonomy-readiness",
    ":dry-health-pass",
    ":coding-readiness",
    ":operator-task",
    ":task-begin",
    ":conflicts",
    ":budget-status",
    ":budget-window-status",
    ":budget-kill-switch",
    ":state-store-drill",
    ":release-audit",
    ":supply-chain-audit",
    ":model-usage",
    ":team-plan",
    ":team-run",
    ":architecture-audit",
    ":model-registry-audit",
    ":model-discovery-audit",
    ":provider-catalog-refresh",
    ":approval-list",
    ":approval-triage",
    ":best-next-action",
    ":self-issue-verify",
    ":ack",
    ":ack-list",
    ":ack-clear",
    ":approval-run",
    ":self-apply-run",
    ":self-build-produce",
    ":self-split",
    ":self-task-propose",
    ":self-task-build",
    ":value-review",
    ":value-review-list",
    ":task-add",
    ":schedule-disable",
    ":schedule-tick",
    ":auto-status",
    ":source-library",
    ":source-registry",
    ":source-review-plan",
    ":implementation-plan",
    ":patch-proposal-plan",
    ":self-build-propose",
    ":self-build-supervisor",
    ":connectors",
    ":connector-plan",
    ":models",
    ":ingest-web",
    ":ingest-rss",
    ":ingest-source",
    ":ingest-project",
    ":remember",
    ":forget",
    ":propose-repair",
    ":repair",
    ":help",
    ":quit",
)


def render_help() -> str:
    """The full ``:help`` page, without a trailing newline.

    Callers print it (``print(render_help(), file=sys.stderr)``), which supplies
    the final newline -- matching what ``main.py`` emitted before the extraction.
    """
    out: list[str] = []
    for entry in HELP_LAYOUT:
        kind = entry[0]
        if kind == "blank":
            out.append("")
        elif kind == "raw":
            out.append(entry[1])
        else:
            _, canonical, left, column = entry
            spec = lookup(canonical)
            if spec is None:  # pragma: no cover - guarded by the tests
                raise KeyError(f"help layout references unknown command {canonical}")
            out.append("  " + left + " " * (column - 2 - len(left)) + spec.description)
    return "\n".join(out)


def render_startup_commands() -> str:
    """The ``Commands: …`` tail of the REPL readiness banner."""
    return BANNER_PREFIX + BANNER_SEPARATOR.join(BANNER_TOKENS)


def layout_tokens() -> frozenset[str]:
    """Every ``:token`` the help layout mentions, rendered or verbatim."""
    import re

    found: set[str] = set()
    for entry in HELP_LAYOUT:
        if entry[0] == "cmd":
            found.add(entry[1])
        elif entry[0] == "raw":
            found.update(re.findall(r"(?<![\w-])(:[a-z][a-z0-9-]*)(?![\w-])", entry[1]))
    return frozenset(found)
