# STATUS: DRAFT / TARGET (not implemented)
# knowledge/doctrine/future/MIGRATION_PATH.md

## Purpose

This document defines the **structure and rules** for `MIGRATION_PATH.md` itself, i.e., how the migration protocol must be written and what it must cover to ensure **agent identity continuity across upgrades** in the *TARGET model* (not the current code).

This is **design intent**: it is **not** evidence that any migration capability exists today. No implementation module is named here as providing this behavior.

---

## Scope

This doctrine covers:

1. **Document structure**: required sections, ordering, and normative language for this file.
2. **Explicit inheritance model**: what identity components are preserved vs re-verified.
3. **Migration protocol rules**: what gates must be applied, what authority is suspended, and how rollback works.
4. **Identity continuity outcomes**: when the system treats the upgrade as the *same* agent vs a *new* agent inheriting prior responsibilities.
5. **Evidence and audit requirements**: how migration actions are logged and justified without silent mutation.

It does **not** define the detailed schema of memory records; that belongs in `MEMORY_LIFECYCLE_CONTRACT.md`.

---

## Hard invariants (must be preserved by the migration protocol)

These invariants apply when writing the migration protocol content in this file:

1. **Policy Gate remains mandatory**  
   Every material action (including migration steps that change authority, memory trust, budgets, roles, or escalation endpoints) must pass the **Policy Gate**. This protocol must never describe bypassing it.

2. **Human-reserved authority stays human**  
   Merge, budget kill-switch changes, governance changes, and approval of escalated, irreversible, or external actions remain **human-controlled** unless a separately approved policy defines a narrower reversible exception. Steps requiring human approval must be explicitly marked.

3. **Witness vs verification separation**  
   Sub-agent outputs are **witnesses**, not unilateral verification. This protocol must specify that no step both performs a material action and unilaterally certifies it as verified by the same agent.

4. **No governance rewriting by the governed**  
   Governance rules may not be silently or autonomously rewritten by agents they govern. Migration must treat governance changes as governed by the same human-approved procedures and audit rules.

5. **Evidence preservation is non-negotiable**  
   Primary evidence must not be silently destroyed because a later record supersedes its interpretation. This protocol must define how supersession preserves references to prior evidence.

6. **Disputed/high-impact records require escalation**  
   The protocol must define escalation triggers and ensure those triggers route to the human endpoint (per the charter’s “event, not habit” principle).

7. **Opus/deep escalation is gated and logged**  
   Migration-related escalations must be explicitly reason-gated, logged, budgeted, and never self-authorised.

8. **No silent mutation of status transitions**  
   Migration must treat status transitions, corrections, retention/deletion actions, and governance-rule changes as auditable events.

---

## Normative tone and language (for `MIGRATION_PATH.md`)

- **MUST**: required by doctrine/invariants.
- **MUST NOT**: prohibited.
- **SHOULD**: strongly recommended; acceptable deviations only with explicit rationale.
- **MAY**: optional.

This section is a writing rule for this file.

---

## 1. Status banner and non-implementation disclaimer

**STATUS banner:** `STATUS: DRAFT / TARGET (not implemented)`.

This is a **design intent** document for the *TARGET model*. It is **not** evidence that an implemented migration capability exists in the current codebase. Where this protocol refers to systems (policy evaluation, approval workflows, memory storage), it does so as **conceptual dependencies** only; no un-named module is asserted to exist.

---

## 2. Goals and non-goals

### Goals
1. **Identity continuity across upgrades**  
   Define what it means to keep the “same agent” across a replacement of runtime/model/configuration and what must be re-established.
2. **Explicit inheritance vs re-verification**  
   Use a component-by-component decision table to prevent hidden trust transfer.
3. **Safe authority handling**  
   Ensure authority is suspended as needed, remains human-reserved where required, and is never widened by migration.
4. **Auditability and evidence preservation**  
   Ensure every continuity decision is justified with linked evidence, and every status transition is auditable without silent mutation.

### Non-goals
1. **No production automation claim**  
   This document does not claim any implemented unattended migration process exists today.
2. **No cryptographic proof-of-truth**  
   The protocol does not claim cryptography can prove external events occurred—only that integrity/attribution may be established for recorded actions.
3. **No implication of existing evaluation harnesses**  
   The protocol defines conceptual comparison and thresholding; it does not claim specific tooling exists unless explicitly named.

---

## 3. Definitions: identity continuity components (the “identity vector”)

The migration protocol reasons over an **Identity Vector** composed of components. Each component is separately classed as **preservable**, **re-verify**, **re-derive**, or **suspend** (see matrix below).

Minimum identity vector components:

| Component (Identity Vector Field) | Description | Preservable vs re-validation basis |
|---|---|---|
| `agent_id` (stable agent identifier) | Stable identity token used by the organisation to refer to the agent. | **Preservable** only if continuity decision accepts “same agent” (constraints below). |
| `role` | Durable role contract category (e.g., research/repair/review/finance/operations). | **Re-verify** role contract applicability under new policy/constitution versions; **Preserve** role label if contract definition unchanged or re-validated. |
| `authority_scope` | Authority boundaries and permitted material actions (including tool/budget/approval scope). | **Suspend** authority during migration; **Restore gradually** only with checks and required approvals. |
| `obligations` | Required duties, SLA-like expectations, and “unfinished work” representation. | **Preserve** unfinished work objects only when continuity acceptance criteria hold; otherwise **re-derive** via reconciliation. |
| `audit_history` | Append-only log references for material actions, status transitions, approvals, and evidence links. | **Preserve** journal entries and references; **Reconcile** operational view mappings without rewriting history. |
| `policy_constitution_version` | Version identifiers for policy/constitution used to evaluate authority, trust, and escalation. | **Re-verify** continuity under new versions; **Record** versions involved in audit. |
| `trusted_relationships` | Which principals/entities the agent may trust for claims/evidence admissibility and how trust is evaluated. | **Re-verify** under new policy; **Preserve** only if trust evaluation rules unchanged or re-validated. |
| `memory_state_view` | Explicit memory view reference: (a) journal continuity (append-only record), (b) operational view derived from it. | **Preserve** journal references; **Re-derive** operational view for new runtime/policy; do not silently mutate journal. |
| `runtime_model_config` | Underlying model/runtime/provider configuration identifiers. | **Re-derive** behavioral expectations; identity continuity is not assumed to equal model continuity. |

**Explicit statement required by doctrine:**  
A stable agent identifier **cannot** be assumed to equal an LLM instance (or runtime instance). Continuity is determined by the protocol’s decision criteria and approvals, not by the mere presence of the same `agent_id`.

---

## 4. Inheritance vs re-verification matrix (core content)

This matrix maps each identity component to one of: **Preserve**, **Re-verify**, **Re-derive**, **Suspend**.

Legend:
- **Evidence required** = what must be provided/linked in audit records.
- **Policy gate/approval required** = which steps require Policy Gate and where human approval is reserved.
- **Escalation triggers** = conditions that require dispute/high-impact escalation.
- **Continuity acceptance** = what makes the component “accepted” for same-agent continuity.

> Note: “Policy Gate” is a conceptual named gate from the charter; this protocol does not claim a specific module name exists unless elsewhere specified in repository documents.

| Identity component | Action | Constraints (evidence, gates, escalation, acceptance) |
|---|---|---|
| `agent_id` | **Preserve** (only if continuity decision accepts “same agent”) | **Evidence required:** prior continuity decision record; identity binding decision rationale; prior `agent_id` registry mapping; audit log reference. **Policy gate/approval required:** Policy Gate on migration job; **human approval required** if continuity decision is “same agent” and any authority/role/constitution change occurred. **Escalation triggers:** if evidence conflicts on identity binding inputs, or if high-impact governance changes occurred without verified human approval. **Acceptance:** continuity accepted only when binding decision rules (Section 6) are satisfied. |
| `role` | **Re-verify** | **Evidence required:** role contract version identifiers; evidence links showing role applicability under new policy/constitution; any proposed role contract changes. **Policy gate/approval required:** Policy Gate always; **human approval required** for governance/contract definition changes. **Escalation triggers:** role authority includes prohibited scopes under new policy or unresolved contract mismatch. **Acceptance:** role contract equivalence proven by approved mapping or re-admission criteria. |
| `authority_scope` | **Suspend** during migration; **Restore gradually** | **Evidence required:** pre-migration authority snapshot; post-check authority restoration plan; approval references. **Policy gate/approval required:** Policy Gate always; **human approval required** for any authority that is human-reserved (merge, governance change, budget kill-switch, escalations). **Escalation triggers:** any attempt to restore irreversible/external action permissions before checks are complete. **Acceptance:** authority restored only after consistency checks and required approvals complete; otherwise remain suspended. |
| `obligations` | **Preserve** unfinished work *or* **Re-derive** work plan | **Evidence required:** list of unfinished work units; provenance of each obligation; current status transitions (from journal). **Policy gate/approval required:** Policy Gate always; **human approval required** if unfinished work includes high-impact/disputed actions. **Escalation triggers:** obligations depend on disputed evidence or superseded status without resolution. **Acceptance:** obligations carried forward only when their governing evidence and policy admissibility remain consistent with Section 8/12 rules; otherwise re-derive via reconciliation and mark as “unknown/disputed” where needed. |
| `audit_history` | **Preserve** (with operational mapping reconciliation) | **Evidence required:** journal references; supersession links; mapping rules used to form operational view. **Policy gate/approval required:** Policy Gate always; no bypass for status transitions. **Escalation triggers:** any missing audit references for prior material actions. **Acceptance:** all continuity decisions cite journal links; journal remains append-only and unchanged. |
| `policy_constitution_version` | **Re-verify** | **Evidence required:** version identifiers; evidence showing migration evaluated authority and trust under new policy. **Policy gate/approval required:** Policy Gate always; **human approval required** if policy/constitution change affects human-reserved authority behavior. **Escalation triggers:** uncertainty about which policy version governed a prior decision used for continuity. **Acceptance:** continuity acceptance records “policy versions involved” and “evaluation results” for each affected component. |
| `trusted_relationships` | **Re-verify** | **Evidence required:** trust graph definitions as of old versions; trust evaluation rules as of new versions; evidence independence checks. **Policy gate/approval required:** Policy Gate always; **human approval required** if trust changes would enable new evidence sources for high-impact decisions. **Escalation triggers:** trust relies on correlated evidence sources or shared failure modes without allowed boundaries. **Acceptance:** trust relationships carried only if re-validated independence boundaries and admissibility under new policy succeed. |
| `memory_state_view` | **Suspend operational view**; **Preserve journal**; **Re-derive operational view** | **Evidence required:** journal snapshot reference(s); derivation rules; pointers showing how superseded records remain linkable; conflict/quarantine handling. **Policy gate/approval required:** Policy Gate always; **human approval required** for clearing disputes/quarantine or altering evidence admissibility for high-impact records. **Escalation triggers:** any record marked disputed/high-risk would become “trusted/verified” without required reconciliation. **Acceptance:** operational view after re-derivation is consistent, linkable to journal evidence, and never rewrites history. |
| `runtime_model_config` | **Re-derive** behavioral expectations & constraints | **Evidence required:** runtime/config identifiers; behavioral difference measurements outputs (as designed in Section 9); tool behavior constraints evidence. **Policy gate/approval required:** Policy Gate always; **human approval required** if behavioral differences affect identity binding acceptance for obligations/authority. **Escalation triggers:** behavioral changes exceed thresholds or affect high-impact tool usage patterns without resolution. **Acceptance:** if thresholds are met or mitigations applied, continuity may be accepted for “same agent”; otherwise binding becomes “new identity inheritance”. |

---

## 5. Migration phases (staged protocol)

Migration proceeds through named phases. Each phase specifies objective, authority needed, entry/exit conditions, allowed state changes, required audit artifacts, and verification steps.

> Definitions:  
> - **Material action**: any action that changes authority, budgets, trust/admissibility, governance rules, memory statuses, or endpoints for escalations.  
> - **Witness vs verification**: sub-agent outputs are witnesses only; verification must be produced under the applicable evidence policy and without self-certification by the same entity that performed the material action.

### Phase 1 — Pre-migration snapshot & freeze
- **Objective:** Create an immutable reference set of identity vector components and current operational states before changes.
- **Authority needed:** Gated automated process under Policy Gate; **no human approval required** if snapshot is non-material (record-only), but **human approval required** before any freeze that affects active authority use if such freeze is material.
- **Entry conditions:** Migration job initiated with declared target versions.
- **Exit conditions:** Snapshot references exist; ongoing material actions are blocked or routed to safe mode according to authority suspension rules (Phase 4).
- **Allowed state changes:** None to journal/history; only append migration job records.
- **Required audit log artifacts:**
  - `migration_job_id`
  - identity vector component snapshot references (IDs)
  - source policy/constitution/runtime identifiers
  - freeze start timestamp
- **Verification steps:** Cross-check that snapshot references are complete; if missing, escalate as disputed/high-impact.

### Phase 2 — Identity binding decision (“same agent” vs “new agent inherits?”)
- **Objective:** Decide whether the upgraded system represents the **same agent** (inherit all continuity-preserved components) or a **new agent identity** that inherits prior responsibilities under constrained rules.
- **Authority needed:** Policy Gate required; **human approval required** when decision outcome is “same agent” and any authority/role/memory admissibility assumptions are re-verified under changed conditions.
- **Entry conditions:** Phase 1 snapshot complete; target versions declared.
- **Exit conditions:** `identity_binding_decision` recorded with rationale, acceptance criteria, and escalation status.
- **Allowed state changes:** Append audit event(s); set migration mode flags (continuity mode).
- **Required audit log artifacts:**
  - decision rationale
  - mapping of which components are accepted as preserved vs re-verified
  - links to evidence supporting decision
- **Verification steps:** Ensure decision does not rely on unverifiable inference; unresolved disputed evidence must trigger escalation.

### Phase 3 — Memory continuity handling
- **Objective:** Preserve evidence journal references; rebuild only derived operational view; quarantine disputed/high-risk records without destructive rewriting.
- **Authority needed:** Policy Gate required; **human approval required** before clearing disputes, moving disputed items into trusted operational view for high-impact decisions, or modifying evidence admissibility endpoints.
- **Entry conditions:** Identity binding decision mode established.
- **Exit conditions:** New operational view reference produced (derived), with linkability preserved.
- **Allowed state changes:** Operational view derivation metadata may change; journal must not be rewritten.
- **Required audit log artifacts:**
  - continuity-safe memory state reference
  - derivation steps record (rule references, input journal snapshot IDs)
  - evidence link mapping for superseded records
  - quarantine/escalation markers
- **Verification steps:** Check that:
  - no disputed evidence becomes “verified” without reconciliation,
  - operational view never replaces journal history silently.

### Phase 4 — Authority continuity handling
- **Objective:** Suspend authority during migration; restore only what is safe and approved.
- **Authority needed:** Policy Gate required; **human approval required** for human-reserved authority scopes.
- **Entry conditions:** Phase 3 memory view continuity prepared; Phase 2 decision recorded.
- **Exit conditions:** Authority restored to a migration-safe set (or remains suspended).
- **Allowed state changes:** Authority state in operational configuration; update of allowed tool/action endpoints.
- **Required audit log artifacts:**
  - authority suspension ranges and restoration steps
  - approval references for any human-reserved scopes
  - timestamps for each authority transition
- **Verification steps:** Ensure no material action is permitted until required checks complete.

### Phase 5 — Evidence re-validation / reconciliation
- **Objective:** Re-validate evidence-dependent claims that are required to preserve identity continuity; reconcile contradictions.
- **Authority needed:** Policy Gate required; **human approval required** if reconciliation changes high-impact record admissibility or affects disputed/high-impact items.
- **Entry conditions:** Memory operational view derived; authority restoration in safe mode.
- **Exit conditions:** Evidence reconciliation statuses recorded; disputed/high-impact routed for resolution.
- **Allowed state changes:** Append derived reconciliation records and status transitions with links.
- **Required audit log artifacts:**
  - reconciliation job details
  - evidence link preservation confirmation
  - status transition audit entries (never silent)
- **Verification steps:** Witness/verification separation enforced: the entity using evidence for material action must not self-certify verification.

### Phase 6 — Activation of replacement runtime
- **Objective:** Activate the replacement runtime/model/config only after continuity gates succeed.
- **Authority needed:** Policy Gate required; **human approval required** if activation could enable human-reserved external/irreversible actions.
- **Entry conditions:** Phase 4 authority checks passed (safe restoration) and Phase 5 reconciliation completed or quarantined with escalation.
- **Exit conditions:** Replacement runtime active in migration mode; only permitted actions enabled.
- **Allowed state changes:** Runtime/model configuration pointer updates for the agent, under suspended/restored authority constraints.
- **Required audit log artifacts:**
  - runtime activation record with model/config identifiers
  - final activation confirmation
  - any residual unknown/disputed state markers
- **Verification steps:** Ensure the activation doesn’t widen authority beyond restored scope.

### Phase 7 — Post-migration consistency checks
- **Objective:** Measure behavioral differences and confirm continuity acceptance thresholds (design intent; no claim of existing tooling).
- **Authority needed:** Policy Gate required; **human approval required** if thresholds fail and require changing continuity mode or restoring additional authority.
- **Entry conditions:** Replacement runtime active; memory/authority continuity prepared.
- **Exit conditions:** Continuity mode finalized (“same agent” accepted or downgraded to “new identity inheritance”).
- **Allowed state changes:** Update continuity acceptance status; possibly trigger Phase 8 rollback decision.
- **Required audit log artifacts:**
  - behavioral difference measurement report references (or “not available” placeholders with escalation triggers)
  - threshold outcomes
  - continuity mode finalization event
- **Verification steps:** If measurement cannot be performed or produces ambiguous results for high-impact areas, escalate.

### Phase 8 — Rollback decision & rollback procedure
- **Objective:** Decide to commit or rollback changes; if rollback, define what is reverted while preserving journal evidence.
- **Authority needed:** Policy Gate required; **human approval required** for rollback that reverts authority or affects governance/kill-switches.
- **Entry conditions:** Consistency checks complete; rollback triggers may be active.
- **Exit conditions:** System in either committed migration state or rollback-complete state with audit artifacts.
- **Allowed state changes:** Revert operational view/runtime/authority to pre-migration safe set; do not delete journal evidence.
- **Required audit log artifacts:**
  - rollback decision record
  - rollback scope list
  - rollback audit event set (partial migration represented)
- **Verification steps:** Confirm invariants: no silent mutation; evidence preserved; human-reserved authority remains human-controlled.

---

## 6. Identity binding decision rules (“same agent” vs “new agent inherits?”)

### Decision criteria inputs
The binding decision MUST consider, at minimum:
- preserved vs re-verified components from the matrix (Section 4),
- whether any **human-reserved authority** changed or was re-enabled,
- whether memory operational view was re-derived without history mutation,
- evidence reconciliation status (no unresolved disputed high-impact items without escalation),
- behavioral difference measurement outcomes (Section 9).

### Rule set
1. **Continuity permitted (“same agent”)** if ALL are true:
   - `agent_id` continuity is accepted by Phase 2 rationale and required evidence exists;
   - authority scope is restored only after required checks and required human approvals (if applicable);
   - memory continuity is linkable to the journal and operational derivation is consistent;
   - disputed/high-impact records are either resolved under policy or remain quarantined with no material actions depending on them;
   - behavioral difference thresholds are not exceeded for continuity-critical behavior (or mitigations apply with human approval).

2. **Continuity denied (“new agent inherits”)** if ANY are true:
   - unresolved disputed/high-impact evidence would allow material action under the new runtime with insufficient reconciliation;
   - authority scope would require human-reserved permissions without human approval;
   - memory continuity would require silent mutation (journal rewrite) or removal of primary evidence references;
   - behavioral difference measurements exceed thresholds affecting continuity-critical tool behaviors or prompt/instruction deltas without mitigation.

3. **Unfinished work handling**
   - If “same agent”: unfinished work units are preserved with updated operational mappings, and any status transitions are auditable.
   - If “new agent inherits”: unfinished work is re-derived into a constrained “handoff queue” with explicit admissibility limitations; only tasks that do not depend on unresolved disputed evidence are allowed.

### Audit trail differences
- **Same agent continuity:** audit logs show component-preservation acceptance and re-verification evidence links.
- **New agent inheritance:** audit logs show “identity binding denied” rationale and that responsibilities are inherited under new continuity mode; authority restoration is restricted accordingly.

### Clarification (required by doctrine)
Stable identity (`agent_id`) is not assumed to equal an LLM instance. The decision is based on the protocol’s criteria and approvals, not runtime equivalence.

---

## 7. Authority suspension rules

Authority scope transitions are governed by these rules:

1. **During Phase 1 through Phase 6 (migration sensitive window):**
   - **MUST** suspend any capability that can cause material external effects or governance changes.
   - **MUST** enforce that Policy Gate evaluates every material action even if it’s “part of migration.”

2. **Human-reserved authority invariant (explicit):**
   The following remain **human-controlled** during migration:
   - merge actions,
   - budget kill-switch changes,
   - governance changes (including constitution/policy rule updates),
   - approval of escalated, irreversible, or external actions.
   
   Therefore:
   - Any step that would (re)enable these scopes **MUST** require explicit human approval gates and corresponding audit entries.

3. **Gradual restoration:**
   - Authority restored only after: Phase 3 memory continuity and Phase 4 authority continuity handling checks.
   - Restoration must be staged to a “safe mode” first, then expanded if Phase 7 checks succeed and approvals exist.

4. **Permanently blocked unless human-approved:**
   - If the migration introduces policy/constitution changes affecting human-reserved scopes and reconciliation is incomplete, those scopes remain blocked until human-approved resolution.

---

## 8. Memory continuity rules (without re-specifying lifecycle)

This section specifies migration behavior in terms of memory continuity, without re-defining record schema rules.

### Continuity-safe memory state
A memory state is **continuity-safe** when:
1. The **journal** (append-only evidence history) is preserved by reference; superseded records remain linkable.
2. The **operational view** is re-derived from the journal under the new policy/constitution evaluation rules.
3. No disputed/high-risk record is silently transformed into a trusted/verified operational state without required reconciliation and escalation where applicable.
4. Supersession does not delete primary evidence; it only changes operational admissibility/status.

### Journal vs operational view separation
- **Journal**: must remain a historical record of what was recorded.
- **Operational view**: may be recomputed to reflect new interpretation rules, while keeping journal references intact.

### Superseded record linkability
- **MUST** record explicit links from superseded operational representations to the journal record(s) and their evidence references.
- **MUST NOT** replace primary evidence references with “later-only” interpretations.

### Disputed/high-risk records treatment
- Disputed/high-impact records detected during migration:
  - **MUST** be quarantined (excluded from material decision use),
  - **MUST** trigger escalation per Section 12 when they block continuity acceptance or affect high-impact actions.

### No silent mutation of history
- Migration **MUST NOT** rewrite journal entries or silently rewrite status transitions.
- Any operational mapping change must produce auditable migration events.

---

## 9. Behavioral differences measurement policy

This section defines “behavioral difference” for upgrade safety in design terms.

### What to compare
Behavioral difference comparison MUST be based on at least:
1. **Policy/constitution evaluation deltas**  
   How authorization/trust/admissibility decisions change across versions.
2. **Tool behavior constraints**  
   Whether tool invocation constraints, input/output handling, and error semantics are materially different.
3. **Instruction/prompt delta impact**  
   Whether the replacement runtime changes instruction sets affecting compliance-relevant behavior.
4. **Memory access patterns**  
   Whether retrieval/usage patterns could cause inadmissible evidence use or change risk boundaries.

### Evidence to collect
- Policy evaluation results: for the agent’s authority/trust queries (design output must be recorded as evidence links).
- Runtime/tool behavior traces (if available): only as audit artifacts; do not claim validated equivalence without thresholds.
- Derivation logs from memory operational view recomputation.

### Thresholds & escalation triggers (design intent)
- If differences exceed **continuity-critical thresholds** (e.g., change would enable disallowed authority scopes, or would convert disputed into trusted without reconciliation), then:
  - escalate as disputed/high-impact,
  - deny “same agent” continuity unless mitigations are applied with human approval.

### How results affect identity binding acceptance
- Passing thresholds: allows “same agent” acceptance if other criteria also pass.
- Failing thresholds: forces “new agent inherits” (or escalated resolution before re-attempt).

> This doctrine intentionally does not name any existing evaluation harness module.

---

## 10. Auditability requirements (migration as an auditable event)

This protocol MUST treat migration as auditable events and record, at minimum:

1. **migration job identifiers**
   - `migration_job_id`
   - timestamps per phase
2. **versions involved**
   - `policy/constitution` version IDs used for evaluation
   - runtime/model/config identifiers
3. **authority approvals and timestamps**
   - explicit human approval identifiers for human-reserved authority scopes
4. **identity binding decision rationale**
   - which identity vector components were preserved vs re-verified vs re-derived vs suspended
   - references to evidence that justify decisions
5. **memory continuity actions and evidence links**
   - journal snapshot references
   - operational view derivation mapping references
   - quarantine markers and evidence link preservation confirmation
6. **rollback artifacts**
   - if rollback happens: list of reverted scopes and status
7. **final activation confirmation**
   - residual unknown/disputed state retention markers

### Forbidding silent mutation of status transitions
- Migration status transitions (e.g., “suspended”, “restored”, “quarantined”, “resolved”, “operational_view_rederived”, “identity_binding_committed”) are themselves auditable events.
- This protocol MUST NOT describe a migration that changes status transitions without audit artifacts.

---

## 11. Rollback procedure

### Rollback triggers
Rollback MUST be initiated (and escalated if needed) if any of the following occurs:
- identity binding acceptance criteria fail after activation (Phase 7),
- missing or inconsistent audit evidence prevents verifying continuity decisions,
- memory continuity would require journal mutation,
- authority restoration accidentally enabled a prohibited scope,
- disputed/high-impact records would become material prerequisites without resolution.

### Rollback scope
- **Revert operational view pointer** to pre-migration operational view reference.
- **Revert runtime/model/config pointer** to pre-migration runtime mode.
- **Re-suspend authority** to the pre-migration safe set.
- **Preserve journal evidence** and migration audit trail.

### What rollback is NOT allowed to do
- Rollback MUST NOT delete evidence, journal entries, or audit trail records.
- Rollback MUST NOT silently rewrite status transitions already logged.

### Authority needed
- Policy Gate required for rollback actions.
- Human approval required for rollback that affects:
  - human-reserved authority scopes,
  - governance/constitution changes,
  - budget kill-switches,
  - any irreversible/external endpoint changes.

### Partial migration representation in audit logs
If rollback occurs after partial phases:
- audit logs MUST show:
  - which phases completed,
  - which components were re-derived/restored,
  - which components remained suspended/quarantined,
  - the rollback scope boundaries and timestamps.

---

## 12. Escalation and dispute handling

### Disputed/high-impact record definition (migration context)
A record is considered disputed/high-impact during migration if it:
- is marked or inferred to be `disputed`, `unknown`, or `insufficient evidence` for continuity-critical purposes,
- involves authority, governance, budget kill-switch behavior, external irreversible actions,
- would change operational admissibility such that disputed content becomes usable for material decisions without reconciliation.

### Escalation path and gates
- Escalation MUST be routed to the **human endpoint**.
- The escalation itself MUST pass Policy Gate and be reason-gated, budgeted, and logged (per “event, not habit”).
- **Opus/deep escalation** remains gated and never self-authorised.

### Operations prohibited until dispute resolution
Until dispute resolution:
- **MUST** block material actions that depend on disputed/high-impact records.
- **MUST** keep the operational view excluding quarantined records for material decisions.

### Disputed records remain present and non-destructively superseded/quarantined
- Disputed/high-impact records:
  - remain linkable in the journal,
  - may be superseded in operational view representation only with explicit auditable transitions,
  - must not be silently removed.

---

## 13. Compatibility and versioning rules

### Label migrations with versions
Each migration job MUST record:
- policy/constitution version IDs used,
- runtime/model/config identifiers,
- migration protocol doctrine version (this document’s effective version, recorded as an identifier),
- identity binding decision mode (same agent vs new agent inherits).

### Schema changes in memory records
This doctrine does not define memory schemas; however, it prescribes behavior:
- When memory record interpretation changes due to new schema rules or adapters, migration MUST:
  - re-derive operational view from journal,
  - preserve evidence linkability,
  - audit the adapter/rule reference used for derivation.
- Migration MUST NOT claim semantic equivalence if interpretation changes are not validated via reconciliation thresholds (Section 9).

### Document breaking changes
- If a migration would break continuity-critical assumptions (e.g., different trust evaluation semantics), the protocol MUST mark the migration as “continuity denied” unless mitigated and human-approved.

### Audit trail of protocol versions used
- Record the migration protocol version used for the decision logic.
- If protocol behavior differs between doctrines, log the exact doctrine version identifier.

---

## 14. Example migration transcripts (doctrinal examples)

> These examples are descriptive and do not assert any implemented module exists.

### Example 1: “Same agent” continuity with re-derived operational view
**Inputs**
- Identity vector components:
  - `agent_id = A-41` (stable)
  - role `reviewer` unchanged
  - authority scope exists but is restored under safe mode
  - memory state references an existing journal snapshot
  - runtime/model/config changes to `Model-X2`
- Target policy/constitution version increments without governance-rule semantic changes.
- Evidence reconciliation reports no unresolved disputed/high-impact records.

**Decisions (Preserve/Re-verify/Re-derive/Suspend)**
- `agent_id`: Preserve (continuity accepted)
- `role`: Re-verify (contract equivalence under new policy)
- `authority_scope`: Suspend → restore gradually (human-reserved scopes unchanged)
- `memory_state_view`: Preserve journal refs → re-derive operational view
- `runtime_model_config`: Re-derive behavioral expectations; thresholds pass

**Approvals required**
- Policy Gate: yes for all material steps.
- Human approval: required only if any human-reserved authority change is implicated (in this example: no).

**Resulting identity binding conclusion**
- “Same agent” continuity accepted.

**What audit entries would exist**
- Phase 1 snapshot event with `migration_job_id`
- Phase 2 identity binding decision record with rationale and evidence links
- Phase 3 operational view re-derivation record referencing journal snapshot
- Phase 4 authority restoration record
- Phase 7 consistency check record
- Final activation record

---

### Example 2: Continuity denied → “new agent inherits” due to disputed high-impact records
**Inputs**
- `agent_id = A-77` stable
- Role contract unchanged
- Memory journal contains a record that is `disputed` and pertains to a continuity-critical obligation with potential external effect.
- Runtime/model/config changes to `Model-Y3`.
- Behavioral differences could affect memory access patterns.

**Decisions**
- `agent_id`: Suspend acceptance; continuity denied
- `authority_scope`: Suspend (remain constrained)
- `memory_state_view`: Preserve journal; quarantine disputed/high-impact item; re-derive operational view excluding it
- `obligations`: Re-derive unfinished work queue with admissibility limitations
- `runtime_model_config`: Re-derive expectations; thresholds fail or mitigation requires escalation

**Approvals required**
- Policy Gate: yes.
- Human approval: required for any change that would move disputed item into trusted operational view (none occurs automatically).

**Resulting identity binding conclusion**
- “New agent inherits” (responsibilities handed off under constrained queue).
- Material actions depending on disputed record are blocked until human resolution.

**What audit entries would exist**
- Evidence reconciliation escalation record (human endpoint)
- Identity binding decision record: continuity denied rationale
- Quarantine audit event with evidence links
- Authority restoration restricted event
- Residual unknown state marker in final activation record

---

### Example 3: Rollback after authority restoration fails a post-migration check
**Inputs**
- `agent_id = A-12`
- Pre-migration snapshot complete
- Identity binding decision initially suggests “same agent”
- During Phase 7 post-migration consistency checks:
  - a continuity-critical threshold fails
  - authority restoration inadvertently exceeded allowed scope for a prohibited action class

**Decisions**
- Continue migration activation only in safe mode until checks conclude.
- Trigger rollback due to authority mis-scoping and/or threshold failure.

**Approvals required**
- Policy Gate: yes.
- Human approval: required if rollback affects human-reserved authority scopes (assumed yes if governance endpoints were touched); otherwise, still logged and gated.

**Resulting identity binding conclusion**
- Final: rollback completed; identity binding remains “not committed” or downgraded depending on which parts were committed before detection.

**What audit entries would exist**
- Phase 7 failure threshold report
- Rollback decision record with rollback scope list
- Authority re-suspension event
- Final audit confirmation: partial migration represented and residual unknown states retained

---

## Open Questions

1. **Ontological basis of identity: memory-based vs hybrid**  
   - **Decision needed:** Whether agent identity in the TARGET model is “ontologically memory-based” (research hypothesis) or hybrid with additional runtime/architecture commitments.  
   - **Why it affects continuity:** It changes which components can be preserved vs must be re-verified when runtime/model changes.  
   - **Evidence required:** Doctrine-aligned evidence about which behaviors can be reconstructed from journal + policy evaluation alone, and which depend on runtime/system instructions.

2. **Model/provider runtime change: identity-preserving or identity-altering**  
   - **Decision needed:** When replacing underlying model/provider/runtime configuration, is the agent treated as the same agent with upgraded capabilities, or a new identity inheriting responsibilities?  
   - **Why it affects continuity:** It determines Phase 2 binding rule outcomes for `runtime_model_config` and downstream authority restoration scope.  
   - **Evidence required:** Behavioral difference measurement outcomes (design thresholds) and policy evaluation deltas tied to tool behavior and compliance-critical prompt/instruction deltas.

3. **Sufficient measurement of behavioral differences**  
   - **Decision needed:** What measurement granularity and thresholding are sufficient to accept “same agent” continuity without hidden authority widening?  
   - **Why it affects continuity:** Overly weak thresholds increase risk of silent behavioral drift; overly strict thresholds cause frequent identity denial.  
   - **Evidence required:** Defined comparison artifacts (policy deltas, tool trace summaries, instruction delta impacts) and examples that map measurement outcomes to acceptable risk.

4. **Trusted relationships carry-forward limits**  
   - **Decision needed:** How much of `trusted_relationships` can carry forward across upgrades without full re-verification?  
   - **Why it affects continuity:** Trusted relationships govern evidence admissibility; mis-carrying trust can convert witnesses into effectively verified sources.  
   - **Evidence required:** Independence boundary analysis for evidence sources, and policy-defined trust evaluation rules across versions.

---

## Governance: how this doctrine itself should change

Changes to this doctrine must:
1. preserve the hard invariants listed in this document,
2. update the identity vector components and matrix/phase rules if the TARGET identity components change,
3. maintain the **no implementation claims** rule (this file4. Any proposed modifications should be treated as governance-rule updates for doctrine—reasoned, logged, and human-approved under the same authority constraints described in the charter.
