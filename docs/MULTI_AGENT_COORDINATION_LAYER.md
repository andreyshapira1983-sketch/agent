# Multi-Agent Coordination Layer

> **Status:** architectural proposal. This capability is not yet implemented.
> Current code remains authoritative. This proposal extends, but does not replace,
> `docs/SUBAGENT_LIFECYCLE.md` and central-agent governance.

## 1. Purpose

The project already has a central `AgentLoop`, bounded one-shot subagents, tool and budget restrictions, verification, memory governance, and a subagent lifecycle specification. The missing architectural layer is durable, auditable coordination between multiple agents working on the same project.

Today a central agent can delegate a task and receive a result. That is not enough for longer multi-agent work. Several agents need a shared mechanism to:

- exchange requests and responses without copying entire conversations;
- preserve the reason behind important decisions;
- maintain one common backlog;
- attach evidence, verification status, and human approval to results;
- prevent context drift between models, sessions, and tools;
- allow a human operator to inspect and correct the process;
- avoid treating every agent message as trusted shared memory.

The target capability is a **Multi-Agent Coordination Layer**: an external, provider-agnostic control plane for messages, decisions, tasks, evidence, and human feedback.

## 2. What this layer is not

It is not a replacement for the central agent, persistent memory, episodic memory, procedural memory, the Source Registry, the Policy Gate, or the verifier.

It does not give subagents independent authority. It does not make agent statements true merely because they were placed in a shared database. It does not allow agents to silently rewrite governance files, install hooks, or change workflow policy.

The central agent remains the owner of delegation, permissions, memory writes, final verification, acceptance, and user-facing output.

## 3. Core idea

The coordination layer consists of five related ledgers:

1. **Agent Mail** — request and response messages between agents.
2. **Decision Log** — durable engineering decisions and their reasons.
3. **Shared Backlog** — tasks, dependencies, owners, status, and acceptance criteria.
4. **Evidence and Verification Links** — references to receipts, files, tests, sources, and verifier outcomes.
5. **Human Feedback Queue** — operator corrections, approvals, vetoes, and priorities.

Each record receives a stable identifier. Agents exchange identifiers rather than repeatedly copying large context blocks.

Example identifiers:

- `REQ-0042` — a request from the central agent to a researcher;
- `RES-0042-01` — a response to that request;
- `DEC-0017` — an approved architectural decision;
- `TASK-0128` — a backlog item;
- `FB-0031` — human feedback;
- `VER-0094` — a verification result.

## 4. Agent Mail

Agent Mail is an append-only message system for agent-to-agent communication.

A request should contain:

- sender and intended recipient role;
- parent trace or task identifier;
- objective;
- bounded context references;
- allowed tools and budget;
- expected output format;
- acceptance criteria;
- deadline or stop condition;
- sensitivity and trust classification.

A response should contain:

- request identifier;
- claimed result;
- files, sources, receipts, or tests used;
- unresolved uncertainty;
- proposed next actions;
- self-reported confidence;
- explicit statement of whether any durable state was changed.

A response is only a message. It is not automatically a verified fact, approved decision, semantic-memory record, or reusable procedure.

## 5. Decision Log

The Decision Log preserves not only what was decided, but why.

A decision record should include:

- decision identifier and version;
- title and status;
- problem being solved;
- considered alternatives;
- selected option;
- rationale;
- evidence and linked requests;
- risks and known limitations;
- approver;
- implementation references;
- superseded decision, if any;
- review date or invalidation condition.

Recommended statuses:

- `proposed`;
- `under_review`;
- `human_approved`;
- `implemented`;
- `superseded`;
- `rejected`;
- `expired`.

Agents may propose decisions. Only the authority defined by governance may approve them. A model-generated proposal must never be presented as a human-approved decision.

Code comments and planning documents may refer to decision identifiers, but the identifier must point to the canonical record. The comment itself is not the source of truth.

## 6. Shared Backlog

The Shared Backlog is the common operational state for the central agent and all bounded subagents.

Each task should include:

- task identifier;
- parent goal and decision links;
- description;
- task type and risk level;
- owner role;
- dependencies and blockers;
- allowed scope;
- acceptance criteria;
- required verifier;
- current status;
- evidence links;
- human priority;
- budget and deadline.

Recommended statuses:

- `proposed`;
- `ready`;
- `in_progress`;
- `blocked`;
- `awaiting_verification`;
- `awaiting_human_review`;
- `accepted`;
- `rejected`;
- `cancelled`.

Agents must not mark their own work as finally accepted. They may mark it `awaiting_verification`. Final acceptance belongs to the verifier, central agent, or human authority specified by the task contract.

## 7. Trust model

The main risk of shared memory is trust collapse: one agent writes an incorrect conclusion and other agents treat it as established truth.

Every coordination record therefore needs an explicit trust class.

Suggested classes:

- `agent_message` — unverified statement from an agent;
- `tool_observation` — output tied to a real tool receipt;
- `source_claim` — statement extracted from a named source;
- `verified_result` — checked against acceptance criteria;
- `human_feedback` — operator input, not automatically a factual claim;
- `human_approved_decision` — authoritative project decision;
- `quarantined` — suspicious, conflicting, or injection-tainted content;
- `superseded` — no longer current.

Trust must not increase merely because the same statement was repeated by several agents that all copied the same source. Provenance and independence of evidence matter.

## 8. Relationship to the existing memory system

The coordination layer and the agent memory system have different responsibilities.

### Working memory

Working memory contains temporary conversation state for one active session. Agent Mail identifiers may be placed into working context, but the complete coordination history should remain external.

### Persistent semantic memory

A message, task, or decision must not automatically become a semantic fact. Promotion into persistent semantic memory must pass the normal knowledge and memory write policies.

### Episodic memory

A completed multi-agent operation may create an episode only after the run outcome is determined. An unverified response or unfinished review must not be recorded as successful experience.

### Procedural memory

A coordination workflow may become a reusable procedure only after repeated verified success. One successful exchange is insufficient evidence.

### Source Registry

Sources and claims referenced by coordination records should use the existing Source Registry instead of creating a parallel evidence store. Coordination records should link to source and claim identifiers.

### Decision Log

The Decision Log is not generic semantic memory. It is a governed record of project intent, authority, and rationale. It requires versioning, approval, and supersession semantics.

## 9. Central-agent authority

The central agent remains responsible for:

- deciding whether delegation is necessary;
- creating the subagent contract;
- limiting context, tools, budget, and lifetime;
- determining which coordination records a child may read or write;
- preventing access to unrelated or sensitive records;
- selecting the verifier;
- reconciling conflicting agent responses;
- presenting uncertainty honestly;
- deciding whether verified results may enter durable memory;
- escalating irreversible or high-risk decisions to the human operator.

Subagents must not communicate around the central authority to obtain additional permissions, expand scope, approve one another's work, or write directly into protected memory.

## 10. Human Workbench

A Workbench-style interface would provide the human operator with one place to inspect:

- active requests and responses;
- agent identities and roles;
- backlog state;
- decision history;
- linked evidence and verification;
- conflicts and quarantined records;
- budgets and usage;
- pending approvals;
- human feedback;
- changes to durable memory.

The operator should be able to:

- approve, reject, or amend a proposed decision;
- veto a result;
- add feedback linked to a task or request;
- freeze an agent or coordination channel;
- revoke permissions;
- mark records as superseded or quarantined;
- inspect exactly why another agent received a particular context package.

The Workbench must not hide failed runs or silently rewrite history. Corrections should create new records that supersede old ones.

## 11. Safety and governance requirements

The coordination layer must obey the same governance boundary as the rest of the agent.

Required controls:

- append-only audit trail for messages and decisions;
- stable correlation identifiers;
- role-based read/write permissions;
- workspace and project isolation;
- redaction of secrets and personal data;
- injection scanning for imported messages and documents;
- immutable tool receipts;
- explicit verification state;
- human approval for high-risk decisions;
- no silent changes to `AGENTS.md`, `CLAUDE.md`, hooks, or policy files;
- read-only audit mode;
- kill switch and budget limits;
- deterministic export for incident review;
- retention and hygiene rules that preserve approved decisions.

An external coordination package must be treated as an untrusted supply-chain component until its code, hooks, commands, data paths, telemetry, and permissions are reviewed.

## 12. No-learning and audit mode

A user instruction or execution policy that prohibits durable learning must also define the behavior of the coordination layer.

In strict no-learning or audit/read-only mode:

- agents may read authorized existing coordination records;
- temporary in-memory communication may continue;
- no new durable messages, decisions, tasks, feedback records, or metadata updates are written unless explicitly allowed by the operator;
- no semantic, episodic, procedural, profile, assumption, or consolidation writes occur;
- read counters and last-access timestamps must not mutate durable state;
- the final answer must disclose that the run was read-only.

If operational messaging must remain durable during audit, that exception must be explicit, minimal, and separated from learning records. It must not be left ambiguous.

## 13. Conflict handling

Agents may disagree. The system must preserve disagreement instead of overwriting one answer with another.

A conflict record should include:

- competing claims;
- provenance of each claim;
- independent evidence status;
- verifier result;
- affected tasks and decisions;
- temporary operating rule;
- human resolution, when required.

Until resolution, disputed claims must not be promoted to verified memory or used as the sole basis for high-risk action.

## 14. Suggested implementation phases

### Phase 0 — documentation and threat model

- approve the coordination contract;
- define trust classes and authority;
- decide whether messages are durable during audit;
- define data retention and privacy boundaries.

### Phase 1 — local append-only Agent Mail

- local SQLite or JSONL store;
- request/response identifiers;
- role-based access;
- parent trace linkage;
- no automatic semantic-memory promotion.

### Phase 2 — Decision Log and Shared Backlog

- versioned decisions;
- human approval states;
- task dependencies and acceptance criteria;
- links to existing source and receipt identifiers.

### Phase 3 — verifier integration

- responses enter `awaiting_verification`;
- verifier outcomes are separate records;
- only verified results may be considered for acceptance or learning.

### Phase 4 — operator Workbench

- read-only inspection first;
- then controlled feedback and approval actions;
- no direct unrestricted database editing.

### Phase 5 — optional external-agent adapters

- Claude, Codex, local models, and other providers communicate through adapters;
- provider-specific prompts remain outside the canonical protocol;
- every adapter must enforce the same permissions and record schema.

## 15. Acceptance criteria

The proposal is ready for implementation only when tests can prove that:

1. Two agents can exchange a request and response by stable identifier.
2. A response is not treated as verified until a verifier record exists.
3. One agent cannot read another task's restricted context.
4. One agent cannot approve its own result.
5. Repeated copied claims do not increase trust without independent evidence.
6. A human-approved decision is versioned and cannot be silently overwritten.
7. Superseded decisions remain auditable.
8. No-learning mode produces zero unauthorized durable-state changes.
9. Audit/read-only does not update counters or timestamps.
10. Quarantined content cannot enter semantic or procedural memory.
11. Communication failure does not become a false successful episode.
12. The central agent can stop, revoke, or quarantine a subagent immediately.
13. The Workbench displays failures, conflicts, and pending verification.
14. External adapters cannot bypass the Policy Gate or memory policies.
15. Normal, explicitly approved collaboration still works after all restrictions are enabled.

## 16. Relationship to Agent Mesh

This proposal was inspired by the public Agent Mesh concept: agent mail, request/response identifiers, a shared decision log, a backlog, and a human Workbench. The useful architectural insight is the need for a coordination layer outside individual model conversations.

This document does not claim that Agent Mesh is already integrated, audited, or adopted by this project. Any future reuse of that project must follow supply-chain review and the governance requirements above.

## 17. Final principle

Shared context is not automatically shared truth.

A reliable multi-agent system needs separate records for communication, evidence, verification, decisions, tasks, and learning. Agents may exchange information freely within their contracts, but only the governed verification and approval path may convert that information into trusted project state or long-term memory.
