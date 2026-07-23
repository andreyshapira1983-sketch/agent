# Corporate Model — FUTURE / TARGET (not current implementation)

> **⚠️ STATUS: FUTURE / ASPIRATIONAL. This document describes a TARGET model,
> NOT what the code does today.** Nothing here should be read as an implemented
> capability. It lives under `docs/future/` on purpose. For what actually
> exists today, read `docs/ROADMAP.md` and `docs/CENTRAL_AGENT_GOVERNANCE.md`;
> when those and this document disagree about the present, they win.

This document is the logical home for the long-horizon "autonomous organisation"
idea (sometimes called Level 5): many specialised agents coordinating under a
central agent, with distinct identity, memory, authority, budget, and
accountability.

It is intentionally separate from the current, human-gated single-agent system.
It is an internal architectural hypothesis, not evidence that such a system is
already safe, reliable, commercially viable, or validated by scientific or
industry consensus.

---

## Where we are today (honest baseline)

- **One** central `AgentLoop`, operated by a human through `main.py`.
- Sub-agents are **bounded child loops**, not independent agents: they share the
  parent's Policy Gate and Model Router/budget, carry **no** persistent memory
  or identity, get one planning attempt, and have the verifier disabled — the
  parent reviews their output as a *witness, not a verified source*
  (`core/subagent_runner`).
- Every applied code change is **human-approved** (`:approval-approve` +
  `:self-apply-run`). There is no unattended self-modification.

That baseline is the floor this future model would have to build on **without
weakening** any existing gate.

---

## Target model (not built)

1. **Per-agent identity.** Each agent has a stable identifier, role, authority
   scope, ownership record, obligations, and audit history distinct from the
   central agent.
2. **Per-agent memory.** Each agent has isolated persistent and episodic memory,
   with explicit rules governing admission, retrieval, retention, sharing,
   supersession, and deletion. This is not a single ungoverned shared store.
3. **Per-agent budget.** Each agent has an independent resource envelope and
   kill-switch, so one agent cannot drain the whole system.
4. **Self-directed coordination.** The central agent delegates, reconciles
   conflicting sub-agent claims, requests verification, and composes results —
   beyond today's single dry-run `:team-plan` / bounded `:team-run`.
5. **Organisational roles.** Durable specialised roles (for example research,
   repair, review, synthesis, finance, and operations) have explicit contracts
   and performance ledgers that can constrain future delegation
   (`core/subagent_registry` is an early, read-only seed of this idea).

The presence of several agents does not by itself create an organisation. The
organisation exists only when roles, authority, budgets, memory boundaries,
verification duties, escalation paths, and accountability are explicit and
enforced.

---

## Epistemic governance is a prerequisite for per-agent memory

Per-agent memory must not be treated as a storage feature alone. Before agents
receive durable independent memory, the system needs a governed process for
answering:

- what was observed, asserted, inferred, or externally supplied;
- who or what produced the record;
- which evidence supports it;
- which scope and time period it applies to;
- whether it conflicts with other records;
- whether it has been superseded;
- which agents may access or act on it;
- what level of risk permits its use.

Every durable memory record should therefore carry, at minimum:

- a stable record identifier;
- provenance and producer identity;
- creation and observation timestamps;
- claim type and domain;
- evidence references;
- applicability and expiry information;
- epistemic status;
- contradiction and supersession links;
- access and sharing policy;
- the reason for the latest status transition.

A provisional status vocabulary may include `observed`, `asserted`, `verified`,
`disputed`, `superseded`, `rejected`, and `unknown`. This vocabulary is a
working design proposal, not a proven universal taxonomy. The detailed schema
and transition rules belong in a separate `MEMORY_LIFECYCLE_CONTRACT.md`specification.

### Verification principles

- A statement made by an agent is a **claim**, not a fact merely because the
  agent is confident or authoritative.
- Provenance is necessary for evaluating trust, but provenance alone does not
  establish truth.
- The number and type of required verification paths depend on the claim class,
  cost of error, reversibility, and policy-defined risk.
- Multiple agents do not constitute independent evidence when they use the same
  source, model, logs, assumptions, or failure mode.
- Independence must be established through different primary evidence,
  observation mechanisms, information boundaries, and privilege separation.
- The system must be able to preserve `unknown`, `insufficient evidence`, and
  unresolved conflict without converting uncertainty into confidence.
- The central agent may submit a claim, attach evidence, and request a status
  change, but it must not directly mark its own material claim as `verified`.

The future design should separate at least two memory views:

1. **Chronological evidence journal.** An append-only history of what was
   recorded, by whom, when, and from which source. It establishes the history
   of registration, not automatically the truth of the recorded content.
2. **Current operational view.** A revisable representation of what the
   organisation may presently use for decisions, including status,
   supersession, scope, freshness, and risk limitations.

Old records are not silently rewritten. A replaced record remains in the
journal and is linked to the record that superseded it.

---

## Auditability and evidence preservation

A production version of the target model must make material agent actions
inspectable after the fact.

Required properties:

1. **Tamper-evident history.** Material actions and memory transitions are stored
   in an append-only or otherwise tamper-evident record. Cryptographic chaining
   may prove sequence integrity inside a log, but it does not prove that the
   content is true or that an external event occurred at the claimed time.
2. **Human-readable explanation.** A non-engineer with the appropriate role
   must be able to understand what action occurred, under which authority, with
   which evidence, and why it was allowed.
3. **Evidence-preserving forensic reconstruction.** The system records enough
   information to reconstruct the observable chain of action: inputs, model and
   policy versions, memory snapshot references, prompts or instructions, tool
   calls, tool responses, approvals, outputs, and status transitions. This does
   not require a future LLM invocation to reproduce bit-identical text.
4. **Cryptographic attribution where appropriate.** Signed records may establish
   integrity and association with a managed key. They support accountability,
   but do not by themselves establish truth, correct authorization, or legal
   non-repudiation.
5. **No silent mutation.** Status changes, corrections, redactions, retention
   actions, and governance-rule changes are themselves auditable events.

Auditability is not a substitute for correctness. A perfectly preserved error
remains an error.

---

## Identity continuity across model upgrades (open question)

A stable agent identity cannot be assumed to be identical to a particular LLM
instance. Replacing the underlying model raises an unresolved question: is the
result the same agent with upgraded capabilities, or a new agent inheriting the
previous agent's responsibilities and records?

Possible components of continuity include:

- persistent agent identifier;
- role and authority scope;
- obligations and unfinished work;
- audit history;
- policy and constitution version;
- trusted relationships;
- explicit memory state;
- underlying model and runtime configuration.

The project does **not** currently adopt the claim that memory alone is the
ontological basis of agent identity. That is one research hypothesis among
several. Some behaviour remains embedded in model weights, system instructions,
runtime architecture, and provider behaviour and may not be transferable as
explicit memory.

Any future model replacement must therefore use an explicit inheritance and
migration protocol that defines:

- what is preserved;
- what is re-verified;
- what authority is suspended during migration;
- how behavioural differences are measured;
- when the replacement is treated as a new identity;
- how rollback is performed.

The detailed protocol belongs in a separate `MIGRATION_PATH.md` document.

---

## Hard invariants any future version must preserve

These are **not** negotiable when this model is eventually built:

- The **Policy Gate** remains a pre-execution checkpoint for every material
  action; no agent may widen policy for itself.
- **Human-reserved authority** remains in place: merge, budget kill-switch,
  governance changes, and approval of escalated, irreversible, or external
  actions stay with a human unless a separately approved policy explicitly
  defines a narrower reversible exception.
- A sub-agent claim remains a **witness**, verified under the applicable
  evidence and risk policy before it is believed, persisted as trusted memory,
  or used for a material action.
- No agent may both perform a material action and unilaterally certify that same
  action as successfully verified.
- Governance rules may not be silently or autonomously rewritten by the agents
  they govern.
- Primary evidence must not be silently destroyed merely because a later record
  supersedes its interpretation.
- Disputed or high-impact records must have an explicit escalation path.
- Opus/deep escalation remains **an event, not a habit** — reason-gated, logged,
  budgeted, and never self-authorised.

---

## Explicit non-goals

This project is **not** building:

1. **A fully autonomous AGI corporation.** The human remains owner, final
   authority, and the escalation endpoint for irreversible and high-risk
   decisions.
2. **One controller to rule them all.** The design rejects a single
   all-knowing memory controller whose unsupported judgment becomes truth.
3. **Trust by position, confidence, or provenance alone.** Authority and known
   origin may inform evaluation, but material trust also requires appropriate
   evidence, relevance, freshness, independence, and risk controls.
4. **Black-box memory.** Durable memory admission, retrieval, sharing, status
   changes, and removal must be explainable and auditable.
5. **Automatic self-modification of governance.** Agents may propose changes,
   but governance rules change only through a defined, logged, human-approved
   procedure.
6. **A claim that this architecture is externally proven.** Similar ideas may
   exist in research papers, drafts, products, or prototypes, but architectural
   resemblance is not validation of the whole model.

---

## Architectural dependencies and document boundaries

`CORPORATE_MODEL.md` defines the intended organisational shape and its hard
constraints. It should remain concise and should not become a literature review
or a detailed implementation specification.

Planned companion documents:

- `MEMORY_LIFECYCLE_CONTRACT.md` — record schema, status transitions, admission rules,
  contradiction handling, access control, and verification policies;
- `RESEARCH_PARALLELS.md` — external papers, drafts, products, and prototypes,
  each labelled by evidence status and never presented as proof of the complete
  architecture;
- `MIGRATION_PATH.md` — staged transition from the current single-agent baseline
  to isolated identities, memories, budgets, verification, and coordination;
- agent-role contracts — authority, tools, budgets, memory scope, acceptance
  criteria, escalation conditions, and termination rules for each durable role.

No target capability should be treated as implemented merely because it appears
in this document or has a parallel in external research.

---

## When to read this document

This is a corporate-model / long-term-strategy document. It is **not** required
reading for analysing today's code. The planner should only pull it in for
questions specifically about the future corporate/organisational model or
long-horizon autonomy — not for ordinary "how does the current agent work"
questions.

_Source of facts for the "today" sections: `core/subagent_runner`,
`core/team_*`, `core/policy`, `core/approval*`. Everything under the target,
governance, auditability, identity-continuity, and migration sections is design
intent with no backing implementation unless another current document and
reproducible code/test evidence explicitly say otherwise._
