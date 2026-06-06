# Agent Doctrine

This file is the operator-facing doctrine source of truth for the current
autonomous-agent execution semantics. The broader architecture inventory lives
in `архитектура автономного Агента.txt`; the runnable implementation map lives
in `README.md`.

## Current Execution Semantics

Snapshot date: 2026-06-07.

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
  Note: a separate, distinct path — the `spawn_subagent` tool backed by
  `core/subagent_runner.py` (`SubAgentRunner`) — does run an isolated child
  `AgentLoop` with a restricted tool set. That tool path is wired in
  `main.py` only when the host is not in dry-run mode (`main.py:4282`); it does
  not consume an approved `SubagentProposal`. The two paths are independent.
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
- `:auto-run` rotates the learning root (`. / core / tools / tests / scripts`)
  and the reflection log window (`20 / 40 / 30 / 60`) per 10-minute wall-clock
  bucket. This is a stateless diversification of inputs; it does not change
  approval, budget or execution boundaries.
- Long unattended autonomy remains blocked while approval items are pending or
  persistent hour/day budget enforcement limits are unset. Budget tracking can
  be active while enforcement remains disabled when all persistent limits are
  zero.
- Structured incident on stop is wired. When the autonomous runtime
  (`core/autonomous_runtime.py`) halts a run (budget denial, circuit stop), it
  opens one high-severity `Incident` (`core/incident.py`) into
  `data/incidents.jsonl`, de-duplicated per trigger+module, forcing human
  escalation. `agent_tick.py` provides the incident log to the runtime.
- Clarification gate when stuck is wired into the live loop. When replanning is
  exhausted (`core/loop.py`), the agent runs `core/clarification_gate.py` and,
  if a loop is suspected, prepends concrete clarification questions to the
  answer instead of silently looping. The gate is on by default and can be
  disabled per-loop (`clarification_gate_enabled`).
- Corroboration-based verification is wired. A claim reaches `verified` status
  only when at least two independent sources agree on the same value
  (`core/knowledge_pipeline.py` `ConflictResolver`). Two weak (`unverified`)
  sources echoing each other do not manufacture a verified fact.
- Truth/Hype filter is wired into the knowledge write path. Promotional content
  with no checkable substance (`core/truth_hype_filter.py`) is classified as
  hype and rejected by `KnowledgeWritePolicy`, so marketing noise never becomes
  long-term knowledge.

## Operator Snapshot

The operator state that triggered this doctrine refresh reported (historical
2026-06-04 observation; counts not re-measured in the 2026-06-07 refresh):

- architecture audit: present=17, gap=1
- stale gap: Doctrine and Architecture Source of Truth
- source registry: 191 sources / 913 claims
- persistent memory: 80 records
- approvals: 0 pending
- scheduler_due=0
- budget tracking active with hour/day enforcement disabled

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
