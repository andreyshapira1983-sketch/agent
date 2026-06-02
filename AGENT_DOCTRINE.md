# Agent Doctrine

This file is the operator-facing doctrine source of truth for the current
autonomous-agent execution semantics. The broader architecture inventory lives
in `архитектура автономного Агента.txt`; the runnable implementation map lives
in `README.md`.

## Current Execution Semantics

Snapshot date: 2026-06-02.

- Work Session exists as `core/work_session.py` and the `:work-session`
  command. Its current skeleton runs bounded multi-cycle health/status passes,
  defaulting to dry-run behavior and one controlled autonomous runtime task per
  cycle. It proves timing, budget and circuit-stop mechanics. It is not a
  claim that open-ended multi-hour execution is available.
- SubagentProposal exists as `core/subagent_memory_scope.py` and the
  `:subagent-proposal` command. The agent can decide that a goal may need a
  subagent, produce explicit memory/tool/budget scopes, and optionally submit
  a human approval item. Real subagent execution must not be implied by this
  layer unless a later implementation consumes an approved proposal and runs it.
- CapabilityRequest exists as `core/capability_request.py` and the
  `:capability-request` command. The agent can infer missing capabilities from
  natural language and submit bounded access requests. Connectors, messaging,
  Upwork, premium model use, file access, memory writes and long sessions are
  not activated automatically by a request.
- Smart Memory exists across persistent memory, episodic memory, procedural
  memory and consolidation reports. Persistent records remain governed by the
  Memory Write Policy; episodic/procedural/consolidation records are local
  experience memory used to steer future planning.
- Dynamic Model Catalog exists as provider API model discovery plus a cached
  `config/model_catalog.json` and complexity-tier selection. The cache can be
  refreshed with `:refresh-models`; tier overrides remain operator-controlled.
- Approval Inbox daemon notice exists. On REPL startup, pending items from
  `data/approval_inbox.jsonl` are surfaced as a daemon notice so the operator
  can review them later.
- Long unattended autonomy remains blocked while approval items are pending or
  persistent hour/day budget enforcement limits are unset. Budget tracking can
  be active while enforcement remains disabled when all persistent limits are
  zero.

## Operator Snapshot

The operator state that triggered this doctrine refresh reported:

- architecture audit: present=17, gap=1
- stale gap: Doctrine and Architecture Source of Truth
- source registry: 64 sources / 473 claims
- persistent memory: 21 records
- approvals: 3 pending / 5 total
- scheduler_due=1
- budget tracking active with hour/day enforcement disabled
- pending approvals include OperatorRoutingBugfixer, UpworkJobMonitor and an
  Upwork capability request

These are observed status counts, not permissions. This doctrine update does
not approve pending items, activate capabilities, run subagents, or change
memory data.

## Non-Negotiable Boundaries

- Documentation may describe proposal layers, but must not imply execution
  that has not been implemented.
- Approval submission is not approval. Approval is not execution. Execution
  requires a separate implemented path with explicit scope consumption.
- Connector discovery or capability requests do not grant connector access.
- Dry-run and status loops are the default autonomy posture until approval and
  budget prerequisites are satisfied.
- Memory writes remain policy-gated and must not be changed by doctrine edits.
