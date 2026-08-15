```md
# STATUS: DRAFT / TARGET (not implemented)
# knowledge/doctrine/future/MIGRATION_PATH.md

## Purpose

This document defines the **structure and rules** for `MIGRATION_PATH.md` itself, i.e., how the migration protocol must be written and what it must cover to ensure **agent identity continuity across upgrades** in the *TARGET model* (not the current code).

It is a doctrine document for long-horizon design. It does **not** claim any implemented migration capability exists unless a named module in this repository implements it (none is named here).

---

## Scope

This document covers:

1. **Document structure**: required sections, ordering, and normative language.
2. **Explicit inheritance model**: what identity components are preserved vs re-verified.
3. **Migration protocol rules**: what gates must be applied, what authority is suspended, and how rollback works.
4. **Identity continuity outcomes**: when the system treats the upgrade as the *same* agent vs a *new* agent inheriting prior responsibilities.
5. **Evidence and audit requirements**: how migration actions are logged and justified without silent mutation.

It does **not** define the detailed schema of memory records (that belongs in `MEMORY_LIFECYCLE_CONTRACT.md`).

---

## Hard invariants (must be preserved by the migration protocol)

These invariants apply when writing *any* upgrade/migration protocol in `MIGRATION_PATH.md`.

1. **Policy Gate remains mandatory**  
   Every material action (including migration steps that change authority, memory trust, budgets, roles, or escalation endpoints) must pass the **Policy Gate**. The migration protocol must never describe bypassing it.

2. **Human-reserved authority stays human**  
   Merge, budget kill-switch changes, governance changes, and approval of escalated, irreversible, or external actions remain **human-controlled** unless a separately approved policy defines a narrower reversible exception. The migration protocol must explicitly mark which steps require human approval.

3. **Witness vs verification separation**  
   Sub-agent outputs are **witnesses**, not unilateral verification. The migration protocol must specify that no step both performs a material action and unilaterally certifies it as verified by the same agent.

4. **No governance rewriting by the governed**  
   Governance rules may not be silently or autonomously rewritten by agents they govern. Migration must treat governance changes as governed by the same human-approved procedures and audit rules.

5. **Evidence preservation is non-negotiable**  
   Primary evidence must not be silently destroyed because a later record supersedes its interpretation. Migration must define how supersession preserves references to prior evidence.

6. **Disputed/high-impact records require escalation**  
   The protocol must define escalation triggers and ensure those triggers route to the human endpoint (per the charter’s “event, not habit” principle).

7. **Opus/deep escalation is gated and logged**  
   Migration-related escalations must be explicitly reason-gated, logged, budgeted, and never self-authorised.

8. **No silent mutation of status transitions**  
   Migration must treat status transitions, corrections, retention/deletion actions, and governance-rule changes as auditable events.

---

## Normative tone and language

In the actual `MIGRATION_PATH.md` content, use the following convention:

- **MUST**: required by doctrine/invariants.
- **MUST NOT**: prohibited.
- **SHOULD**: strongly recommended; acceptable deviations only with explicit rationale.
- **MAY**: optional.

This section is a *writing rule* for `MIGRATION_PATH.md`.

---

## Required document structure for `MIGRATION_PATH.md`

The repository’s `knowledge/doctrine/future/MIGRATION_PATH.md` must be written with the following top-level sections, in this order. Each section’s minimum contents are described.

### 1. Status banner and non-implementation disclaimer
- Repeat the status banner format: `STATUS: DRAFT / TARGET (not implemented)` at the top.
- Explicitly state that the protocol is **design intent** and not evidence of implementation.

### 2. Goals and non-goals
- Goals: identity continuity across upgrades; explicit inheritance vs re-verification; safe authority handling; auditability.
- Non-goals: production claims of automation, cryptographic proofs of truth, any implication current systems implement these behaviors.

### 3. Definitions: identity continuity components (the “identity vector”)
Define the identity components that the migration protocol can preserve or re-verify. At minimum include:

- persistent **agent identifier** (stable ID) semantics
- **role** and **authority scope**
- **obligations** and **unfinished work** representation
- **audit history** preservation requirements
- **policy/constitution versioning** treatment
- **trusted relationships** (what they are; how trust is evaluated)
- **explicit memory state** (what kind of memory view; what migration does to it)
- **runtime/model configuration** handling (what is replaced; what is inherited)

This section must explicitly say which aspects are *preservable* and which require *re-validation*.

### 4. Inheritance vs re-verification matrix (core content)
Provide a matrix (table) mapping each identity component to one of:

- **Preserve** (with constraints)
- **Re-verify** (with constraints)
- **Re-derive** (with constraints)
- **Suspend** (temporarily) during migration

Constraints are mandatory: for each row, specify:

- what evidence is required
- what policy gate/approval is required
- what triggers escalation
- what constitutes “acceptance” for continuity

### 5. Migration phases (staged protocol)
Describe migration as phases. Each phase must include:

- objective
- authority needed (human vs gated automated process)
- entry/exit conditions
- allowed state changes
- required audit log artifacts
- verification steps (if any)

Minimum phases (must be present):

1. **Pre-migration snapshot & freeze**
2. **Identity binding decision** (“same agent” vs “new agent inherits?”)
3. **Memory continuity handling**
4. **Authority continuity handling**
5. **Evidence re-validation / reconciliation**
6. **Activation of replacement runtime**
7. **Post-migration consistency checks**
8. **Rollback decision & rollback procedure**

### 6. Identity binding decision rules (“same agent” vs “new agent”)
This section must define explicit decision rules:

- When continuity is permitted (conditions met)
- When continuity is denied and inheritance is treated as a new identity
- How unfinished work is carried forward in each case
- How audit trails differ between “same agent continuity” and “new agent inheritance”

This section must also clarify that stable identity cannot be assumed to equal an LLM instance and must be based on the protocol’s decision criteria.

### 7. Authority suspension rules
This section must enumerate which capabilities/authority scopes are:

- suspended during migration,
- restored gradually after checks,
- permanently blocked unless human-approved.

It must explicitly include the charter’s human-reserved authority invariant.

### 8. Memory continuity rules (without re-specifying lifecycle)
This section must describe *migration behavior* in terms of the memory lifecycle contract, without duplicating that contract.

At minimum, it must state:

- what constitutes a “continuity-safe” memory state
- how the migration references the journal vs operational view separation
- how superseded records remain linkable to preserved evidence
- how disputed/high-risk records are treated (escalation required)

It must include a requirement that migration does not silently rewrite history.

### 9. Behavioral differences measurement policy
This section defines what “behavioral difference” means for upgrade safety in the protocol, including:

- what to compare (policy versions, tool behavior constraints, prompt/instruction deltas, memory access patterns)
- what evidence to collect
- what thresholds trigger escalation or re-verification
- how results affect identity binding acceptance

This section is design intent; it must not claim any existing evaluation harness unless named modules exist (none are named here).

### 10. Auditability requirements (migration as an auditable event)
This section must specify that the migration protocol records, at minimum:

- migration job identifiers
- versions involved (policy/constitution/runtime/model config identifiers)
- authority approvals and timestamps
- identity binding decision rationale
- memory continuity actions and the evidence links they reference
- rollback artifacts if any
- final activation confirmation and any residual “unknown/disputed” state retention

It must explicitly forbid silent mutation of migration status transitions.

### 11. Rollback procedure
This section must include:

- rollback triggers
- rollback scope (what is reverted and what is not)
- authority needed
- how rollback interacts with memory journal preservation
- how “partial migration” is represented in audit logs

### 12. Escalation and dispute handling
This section must define:

- what counts as a disputed or high-impact record in migration context
- escalation path and gates (human endpoint)
- what operations are prohibited until dispute resolution
- how disputed records remain present and non-destructively superseded or quarantined

### 13. Compatibility and versioning rules
Define:

- how to label migrations with policy/constitution versions
- how to handle schema changes in memory records (without claiming schema exist here)
- how to document breaking changes
- how to keep an audit trail of protocol versions used for the migration

### 14. Example migration transcripts (doctrinal examples)
Provide 1–3 worked examples in purely descriptive form, each showing:

- inputs (versions and identity vector components)
- decisions (preserve vs re-verify)
- approvals required
- resulting identity binding conclusion
- what audit entries would exist

These examples are not evidence of implementation.

---

## Structural rules for content completeness (checklist)

When writing `MIGRATION_PATH.md`, ensure all of the following are explicitly answered somewhere in the document:

- [ ] What identity components exist in the “identity vector”?
- [ ] For each component: preserve vs re-verify vs re-derive vs suspend?
- [ ] What phases exist and what changes each phase can make?
- [ ] What determines “same agent continuity” vs “new agent inheritance”?
- [ ] Which actions require human-reserved authority approval?
- [ ] How is the Policy Gate applied to migration steps?
- [ ] How is memory journal vs operational view handled to prevent silent mutation?
- [ ] How are disputed/high-impact items escalated?
- [ ] What evidence is recorded to justify continuity decisions?
- [ ] What rollback steps exist and what constraints apply?

---

## Module naming rule (no unverifiable claims)

This doctrine must never claim a migration capability exists in code unless the protocol text names the relevant module(s).

- If the protocol requires referencing current components, it must cite by module name where possible (example categories might include: policy evaluation, approval workflows, memory storage), but only if such modules are actually named in repository documents.
- If no module is named, describe behavior as design intent only.

---

## Repository integration boundaries

- `CORPORATE_MODEL.md` defines target organisational constraints and invariants.
- `MIGRATION_PATH.md` is the long-horizon identity continuity protocol document.
- `MEMORY_LIFECYCLE_CONTRACT.md` defines memory record lifecycle and schemas; `MIGRATION_PATH.md` must reference it but not duplicate schema definitions.
- Agent-role contracts define authority scopes; `MIGRATION_PATH.md` must reference them as sources of authority/scope definitions (without claiming implementations).

---

## Open questions (must be explicit, not hidden)

`MIGRATION_PATH.md` must include an “Open Questions” subsection listing unresolved design decisions that affect identity continuity, at minimum:

- whether identity is “ontologically” memory-based (research hypothesis vs implemented assumption)
- how to treat underlying model/provider runtime changes as identity-preserving vs identity-altering
- how to measure behavioral differences sufficiently for continuity acceptance
- how much “trusted relationships” can carry forward without re-verification

Each open question must state:
- what decision is needed
- why it affects identity continuity
- what information/evidence is required to resolve it

---

## Governance: how this doctrine itself should change

Changes to this doctrine require:

- preserving the hard invariants listed above
- updating the checklist and structural sections if the target identity components change
- maintaining the “no implementation claims” rule

Any proposed modifications should be treated as governance-rule updates for doctrine—reasoned, logged, and human-approved under the same authority constraints described in the charter.

```
