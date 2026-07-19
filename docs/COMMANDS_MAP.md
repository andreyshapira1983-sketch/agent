# Commands Map — operator (REPL) commands

> **Status of this document:** authoritative map of the **real** operator
> command surface. It is built from the actual REPL dispatch in `main.py`
> (the `if head == ":…"` chain) and the CLI flags in `main.py --help`. Only
> commands that exist in code are listed. If a command is not here, it does not
> exist. Aliases are grouped on one row (`a | b`).

Enter these at the interactive REPL (`python main.py` with no `--ask`). A few
are also reachable one-shot via `--ask ":command args"`.

## CLI flags (one-shot / startup) — `main.py`

| Flag | Purpose |
| ---- | ------- |
| `--ask "<q>"` | One-shot question (no memory). Omit to enter the REPL. |
| `--file <path>` | Optional file hint the planner may `file_read`. |
| `--workspace <dir>` | Workspace root (default: current directory). |
| `--auto-approve off\|approve\|deny` | Approval policy for escalated actions (default `off` = deny in one-shot). |
| `--resume <TRACE_ID>` | Resume a previous run by trace id. |
| `--reason <r>` | Deep/Opus escalation reason (with `--ask`). |
| `--expect <e>` | Expected deep output (used with `--reason`). |

## Memory

| Command | Purpose |
| ------- | ------- |
| `:remember [tags] <text>` | Save a fact/preference to persistent memory. |
| `:forget [id\|all]` | Delete persistent record(s). |
| `:clear \| :reset` | Clear working memory (persistent untouched). |
| `:smart-memory \| :memory-status` | Show episodic/procedural memory summary. |
| `:memory-consolidate` | Consolidate episodic memory. |
| `:audit [on\|off\|status]` | Read-only audit mode: freeze ALL durable memory writes (episodic, procedural, consolidation, user profile, and agent-auto persistent/semantic writes) for the session. Set before investigating memory so the audit cannot contaminate its own object. Operator `:remember` (user-explicit) still works. |
| `:hygiene [subcmd] [--dry-run]` | Memory hygiene (dedup/expire/summarise). |

## Sources, ingestion & knowledge

| Command | Purpose |
| ------- | ------- |
| `:ingest-source <path>` | Ingest one document/file into the Source Registry. |
| `:ingest-project [path]` | Ingest a project tree. |
| `:ingest-web <topic> [--sources …]` | Controlled web learning from the curated library. |
| `:ingest-rss <feed_url> [--limit N]` | Ingest an RSS feed. |
| `:source-library [group\|all]` | Show the curated online source library. |
| `:source-registry \| :source-status` | Show Source Registry + claims. |
| `:source-review-plan <goal>` | Plan a source review for a goal. |
| `:implementation-plan <goal>` | Plan an implementation from sources. |
| `:patch-proposal-plan \| :patch-plan <goal>` | Plan a patch proposal from sources. |
| `:connectors [all\|wired\|partial\|planned]` | List source connectors. |
| `:connector-plan <goal>` | Plan connector usage for a goal. |

## Models & budget

| Command | Purpose |
| ------- | ------- |
| `:models \| :model-routes` | Show active model routes. |
| `:model-registry-audit \| :model-audit` | Audit model registry / active routes. |
| `:refresh-models \| :model-catalog-refresh \| :model-refresh` | Query providers, persist catalog. |
| `:model-discovery-audit \| :discovery-audit` | Read-only model discovery audit. |
| `:provider-catalog-refresh --dry-run` | Dry-run diff vs live provider models. |
| `:model-usage \| :usage-models` | Model usage ledger. |
| `:budget-status` | Current budget status. |
| `:budget-config \| :budget-limits` | Show budget limits. |
| `:budget-window-status \| :budget-windows \| :budget-ledger` | Budget windows. |
| `:budget-kill-switch \| :kill-switch [--clear]` | Inspect / clear the budget kill-switch. |
| `:operator-budget \| :budget-digest` | Operator-facing budget digest. |

## Approvals & alerts

| Command | Purpose |
| ------- | ------- |
| `:inbox` | List pending approvals. |
| `:approval-list [state]` | List approvals by state. |
| `:approve \| :approval-approve <id>` | Approve an inbox item. |
| `:deny \| :approval-deny <id>` | Deny an inbox item. |
| `:approval-run <id>` | Run an approved item. |
| `:approval-abort <id>` | Abort an approval. |
| `:approval-triage \| :triage` | Read-only triage of the inbox. |
| `:ack \| :acknowledge <action>` | Acknowledge an advisory alert. |
| `:ack-list \| :acks` | List acknowledged alerts. |
| `:ack-clear \| :unack <action>` | Un-acknowledge an alert. |

## Self-improvement (self-build / self-repair) — human-gated

| Command | Purpose |
| ------- | ------- |
| `:repair <target> [proposal] [tests] [--pattern]` | Apply/inspect a self-repair. |
| `:propose-repair <target> [tests] [--pattern] [--trace]` | Generate a repair proposal. |
| `:rollback` | Apply/inspect compensation (undo) plans. |
| `:self-build-produce` | Produce a grounded self-apply proposal (TD-025/036). |
| `:self-build-propose <…>` | Produce a self-build proposal (also one-shot). |
| `:self-build-supervisor` | Read-only self-build supervisor cycle. |
| `:self-split <path.py>` | No-LLM incremental module split proposal. |
| `:self-task-propose` | Stage A: task + failing acceptance test (Ступень 1). |
| `:self-task-build <approval_id>` | Stage B: implement one approved task. |
| `:self-apply-run <inbox_id>` | Apply an approved change (trusted lane). |
| `:self-issue-verify <fingerprint>` | Verify a self-improvement issue. |
| `:value-review <item_id> <verdict> [note]` | Human value-review verdict (TD-032). |
| `:value-review-list` | List value-review items. |

## Autonomous runtime, tasks & scheduling

| Command | Purpose |
| ------- | ------- |
| `:auto-run` | Run one autonomous runtime pass. |
| `:auto-status` | Autonomous runtime status. |
| `:work-session \| :work-sess` | Long work session skeleton. |
| `:campaign-start \| :campaign` | Start a 24/48h work campaign. |
| `:campaign-status \| :campaign-ledger` | Campaign status/ledger. |
| `:queue-status` | Task queue status. |
| `:task-add \| :task-list \| :task-run \| :task-cancel` | Manage the persistent task queue. |
| `:scheduler-status` | Scheduler status. |
| `:schedule-add \| :schedule-list \| :schedule-disable \| :schedule-tick` | Manage scheduled tasks (`:schedule-disable` also one-shot). |
| `:capability-request \| :capability-proposal` | Propose a capability request. |

## Operator status, audits & readiness

| Command | Purpose |
| ------- | ------- |
| `:operator-check \| :project-check \| :project-status` | Project status check. |
| `:operator-task <…>` | Run an operator task. |
| `:learn \| :learn-project` | Learn the current project. |
| `:urgent-status \| :operator-urgent` | Urgent items. |
| `:next-actions \| :operator-next` | Suggested next actions. |
| `:best-next-action \| :next-action \| :bna` | Single most important next action. |
| `:autonomy-readiness \| :operator-readiness` | Autonomy readiness report. |
| `:coding-readiness \| :programming-readiness` | Coding readiness report. |
| `:dry-health-pass` | Read-only health-pass collector. |
| `:architecture-audit \| :arch-audit \| :roadmap-audit` | Static architecture gap audit. |
| `:conflicts \| :conflict-status` | Source Registry conflicts. |
| `:state-store-drill \| :state-drill \| :state-recovery-drill` | State-store recovery drill. |
| `:release-audit \| :release-hygiene` | Release artifact hygiene. |
| `:supply-chain-audit \| :supply-audit \| :ci-audit` | Supply-chain / CI audit. |

## Multi-agent / subagents (bounded — see governance doc)

| Command | Purpose |
| ------- | ------- |
| `:team-plan \| :agent-team \| :subagents <goal>` | Dry-run multi-agent team plan. |
| `:subagent-proposal \| :propose-subagent <…>` | Propose a subagent contract. |
| `:team-run \| :team-execute \| :subagents-run <goal>` | Execute bounded subagent contracts. |

## Natural-language routing (intent parity)

> **Status of this section:** authoritative map of the **real** deterministic
> operator-intent router in `core/operator_intent.py` (the ordered
> `if _matches_…` chain) and its detectors in
> `core/operator_intent_patterns.py`. Only routes that exist in code are
> listed. When this section and those modules disagree, the code wins.

Operators do not have to type `:commands`. A plain-language message is first
run through a **deterministic, no-LLM** matcher. If it matches one of the
narrow patterns below, the equivalent operator command is invoked directly
(logged as `operator_intent kind=… command=…`). If nothing matches, the
message falls through to the normal model-backed agent loop unchanged.

| Natural-language phrasing (examples) | Routed to | Intent kind |
| ------------------------------------ | --------- | ----------- |
| "start building yourself", "начни программировать себя" | `:self-build-produce` | `self_build_request` |
| "propose/design a subagent for …" | `:subagent-proposal` | `subagent_proposal` |
| "audit / review the architecture" | `:architecture-audit` | `architecture_audit` |
| "you're missing a connector/capability for …" | `:capability-request` | `capability_request` |
| "find a TODO/FIXME and propose a task with a failing test" | `:self-task-propose` | `self_task_proposal` |
| "draft a patch proposal for …" | `:patch-proposal-plan` | `patch_proposal` |
| "plan a source review for …" | `:source-review-plan` | `source_review_plan` |
| "plan an implementation of …" | `:implementation-plan` | `implementation_plan` |
| "run a safe self-check" | `:operator-check` | `safe_self_check` |
| "are you ready to code / programming readiness" | `:coding-readiness` | `programming_readiness` |
| "what can you do / your capabilities" | `operator-capabilities` | `capability_check` |
| "what are your current gaps" | `operator-gaps` | `current_gaps_check` |
| "find your live weaknesses" | `operator-weaknesses` | `weakness_finder` |
| "what's the next safe test" | `operator-next-safe-test` | `next_safe_test` |
| "check the project health/status" | `:operator-check` | `project_health` |
| "memory status / what do you remember" | `:smart-memory` | `smart_memory_status` |
| "anything urgent" | `:urgent-status` | `urgent_status` |
| "single most important next action" | `:best-next-action` | `best_next_action` |
| "what should I do next" | `:next-actions` | `next_actions` |
| "are you ready to run autonomously" | `:autonomy-readiness` | `autonomy_readiness` |
| "which model / model routing status" | `:models` | `model_status` |
| "budget / token / spend status" | `:operator-budget` | `budget_status` |
| "what's in the approval inbox" | `:approval-list all` | `approval_status` |

### Model-assisted veto (`core/intent_understanding`)

A second, later layer sits **after** a positive match from the table above, at
`main.py:1069`. The keyword matcher works on substrings, so it cannot
distinguish "please run the architecture audit" from a sentence that merely
mentions one — and the latter would be hijacked into a command.

`understand_intent` asks the model whether the message is really a request or
just conversation, and its answer is **only ever able to cancel** the routing:

- it may not choose an action — the candidate is passed in from the kernel;
- it may only pick from the agent's real capability list, so an invented action
  is rejected;
- model error, unparseable output, low confidence, or no model configured all
  leave the deterministic routing untouched.

Kernel decides, model advises. This is why it appears here rather than in the
table: it removes routes, never adds them.

**Safety guarantees (verified in code):** the router is intentionally narrow.
Before any positive match it returns `None` (falls through to the model) for:
empty/very long input, plain bug notes (`_looks_like_plain_bug_note`),
meta-instructions that only *describe* routing (`_looks_like_meta_instruction`),
explicit "do not route / just answer" commands
(`_looks_like_explicit_non_routing_command`), and explicit documentation
requests (`_explicit_documentation_requested`). None of these routes writes
code or applies changes on its own — the self-build / self-task / patch routes
still land on the same human-gated approval flow as their `:command` twins.
Matchers are kept deliberately conservative: broadening one has regressed the
planner before, so every matcher carries paired negative tests in
`tests/test_operator_intent*.py`.

## Help

| Command | Purpose |
| ------- | ------- |
| `:help \| ?` | Show command help. |

_Source of facts: the REPL dispatch in `main.py` and `cli/commands_*.py`
handlers. When a command is added/renamed in `main.py`, update this table.
When this file and `main.py` disagree, `main.py` wins._
