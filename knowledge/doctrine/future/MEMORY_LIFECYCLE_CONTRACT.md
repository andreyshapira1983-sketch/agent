# STATUS: DRAFT / TARGET (not implemented)

# knowledge/doctrine/future/MEMORY_LIFECYCLE_CONTRACT.md

## Purpose

This document proposes the **structure and rules** for the long-horizon, per-agent durable memory system used by the future corporate model.

It defines:

- a durable memory record schema (what fields exist, including provenance, timestamps, and evidence links);
- an epistemic status vocabulary (what the statuses mean);
- **status transition rules** (who may request transitions, under what preconditions, and what must be logged);
- contradiction, supersession, and dispute handling;
- access/sharing constraints and escalation paths;
- audit requirements that preserve forensic reconstruction.

> This is a **TARGET contract**. Nothing in this file is evidence of implemented capabilities.  
> It is intended as the logical home for the future “memory lifecycle” specification, not a description of current code behavior.

---

## Hard invariants this contract must preserve

This contract is subordinate to the corporate model hard invariants and must not conflict with them:

1. **Policy Gate remains mandatory**: every material memory admission, promotion, sharing, deletion/retention change, or escalation request must pass the Policy Gate before it can take effect.
2. **Human-reserved authority remains in place**: merge/budget kill-switch/governance changes and approval of escalated, irreversible, or external actions remain with a human unless explicitly narrowed by a separately approved, reversible policy exception.
3. **Witness vs verified**: sub-agent outputs remain **witnesses** until verified under the applicable evidence and risk policy before being believed, persisted as trusted memory, or used for material action.
4. **No self-certification**: no agent may both perform a material action and unilaterally certify that same action as verified.
5. **Governance rules are not rewritten silently**: agents may propose, but governance changes must follow the defined human-approved process and must be logged.
6. **Evidence preservation is mandatory**: primary evidence must not be silently destroyed merely because a later record supersedes an interpretation.
7. **Disputed/high-impact records have escalation paths**: unresolved conflict or high-impact disagreement must lead to explicit escalation.
8. **Opus/deep escalation is an event**: it must be reason-gated, logged, budgeted, and never self-authorized.

---

## Definitions and scope

### Durable memory record
A **durable** record is a unit of stored information intended to remain available across time horizons for reconstruction and operational use. It is not assumed to be true by virtue of storage.

### Claim vs fact
A memory record expresses a **claim** about some domain. Even “verified” records represent the system’s assessment given evidence and policy at the time, not an absolute truth guarantee.

### Views
The future model distinguishes at least two views:

1. **Chronological evidence journal** (append-only, tamper-evident history of recording and transitions)
2. **Current operational view** (the current decision-use representation derived from the journal, with status/supersession/risk/freshness constraints)

This contract specifies record schema and lifecycle. It does not prescribe the full storage engine implementation.

---

## Record schema (target)

### Storage model assumption (target)
Each durable memory record is represented as:

- **Record Identity** (stable identifier)
- **Header** (routing fields: domain, claim type, producer, target scope)
- **Provenance** (who/what produced it, evidence references, observation times)
- **Epistemic Content** (structured claim + links)
- **Status & Transitions** (current epistemic status and a traceable transition log)
- **Access & Risk** (sharing policy and operational constraints)
- **Retention** (lifespan, expiry, legal hold flags if applicable)

> Field names below are normative for the contract. Implementations may map them to internal storage keys, but must preserve semantics.

---

## 1) Identity and linkage fields

### `record_id`
- **Type**: string
- **Format (target)**: opaque, stable identifier (e.g., UUIDv7-like)
- **Purpose**: unique key for a single durable memory record (not a claim hash requirement)

### `parent_record_id` (optional)
- **Type**: string|null
- **Purpose**: when this record is derived via transformation (e.g., summarization), link lineage.

### `supersedes_record_id` (optional, nullable)
- **Type**: string|null
- **Purpose**: if this record supersedes another for operational use, link the record it replaces.

### `contradicts_record_ids` (list, possibly empty)
- **Type**: array<string>
- **Purpose**: explicit references to records that the epistemic evaluation considers contradictory.

### `dispute_thread_id` (optional)
- **Type**: string|null
- **Purpose**: group conflicting records under a dispute resolution path.

---

## 2) Producer and provenance

### `producer_agent_id`
- **Type**: string
- **Purpose**: stable identifier of the agent (or tool/principal) that created the claim record instance.

### `producer_role` (optional)
- **Type**: string|null
- **Purpose**: role name or contract identity used for accountability.

### `producer_method`
- **Type**: enum
- **Allowed (target)**: `agent_output`, `tool_output`, `human_input`, `imported`, `migration_inheritance`
- **Purpose**: clarifies how the record entered the system.

### `observation_source`
- **Type**: object (contracted shape below)
- **Purpose**: describes the underlying source(s) of evidence that back the claim.

#### `observation_source` fields
- `source_kind`: enum (`log`, `artifact`, `external_observation`, `dataset`, `tool_call`, `human_document`, `memory_journal_entry`, `other`)
- `source_refs`: list of `evidence_ref` objects (defined below)
- `source_independence_group`: string|null (used to prevent “shared failure mode” false independence)

### `evidence_refs`
- **Type**: array<`evidence_ref`>
- **Purpose**: references to evidence items used to support the claim.

#### `evidence_ref` object (target)
- `evidence_id`: string
- `evidence_kind`: enum (e.g., `tool_response`, `prompt_log`, `artifact_checksum`, `external_link`, `human_note`)
- `retrieval_policy_id`: string|null (how evidence may be reloaded/accessed)
- `integrity_fingerprint`: string|null (hash or signature reference; not mandatory but recommended)
- `created_from`: enum (`direct`, `transformed`, `aggregated`)
- `time_of_observation`: ISO-8601 timestamp string|null

### `provenance_notes`
- **Type**: string|null
- **Purpose**: human-readable provenance commentary for audit reconstruction.

---

## 3) Claim content

### `claim_type`
- **Type**: enum (target)
- **Examples (non-exhaustive)**: `factual_observation`, `causal_inference`, `recommendation`, `policy_interpretation`, `decision_commitment`, `operational_state`, `identity_assertion`, `obligation_claim`
- **Purpose**: informs required verification paths and risk handling.

### `claim_domain`
- **Type**: string
- **Purpose**: domain label (e.g., `finance`, `operations`, `research`, `governance`, `identity`, `technical_architecture`)

### `claim_subject`
- **Type**: string|null
- **Purpose**: who/what the claim is about (can be agent id, project, system component, external entity).

### `claim_text` (optional if claim is structured)
- **Type**: string|null
- **Purpose**: canonical human-readable claim statement.

### `claim_struct` (optional)
- **Type**: object|null
- **Purpose**: structured representation when needed for verification, extraction, or evaluation.

### `applicability_window`
- **Type**: object
- **Fields**:
  - `valid_from`: ISO-8601 timestamp string|null
  - `valid_to`: ISO-8601 timestamp string|null
  - `scope`: enum (`global`, `agent_scoped`, `role_scoped`, `project_scoped`, `time_scoped`, `other`) + `scope_ref` string|null

### `risk_class`
- **Type**: enum (target)
- **Examples**: `low`, `medium`, `high`, `critical`
- **Purpose**: determines verification depth and escalation thresholds.

---

## 4) Epistemic status and transition trace

### `epistemic_status`
- **Type**: enum (target)
- **Allowed values (proposal)**:
  - `observed`
  - `asserted`
  - `verified`
  - `disputed`
  - `superseded`
  - `rejected`
  - `unknown`

> Vocabulary is provisional. Future versions may refine names, but must retain auditable semantics and transition meaning.

### `status_effective_time`
- **Type**: ISO-8601 timestamp string
- **Purpose**: when the current status became effective for operational view.

### `status_reason`
- **Type**: string|null
- **Purpose**: human-readable explanation of the reason for the current status.

### `verification_request_id` (optional)
- **Type**: string|null
- **Purpose**: links to the process by which verification was requested/performed (for audit).

### `transition_log`
- **Type**: array of `status_transition` objects
- **Purpose**: append-only history of every status change, including who/what initiated it and under what policy.

#### `status_transition` object (target)
- `transition_id`: string
- `from_status`: enum
- `to_status`: enum
- `initiator_principal`: string (agent id, human id, or system principal)
- `initiator_role`: string|null
- `policy_gate_check_id`: string (reference to the policy evaluation result)
- `timestamp_requested`: ISO-8601 timestamp string
- `timestamp_effective`: ISO-8601 timestamp string
- `transition_kind`: enum (`admit_observed`, `promote_asserted`, `verify`, `dispute`, `supersede`, `reject`, `expire`, `reclassify`, `correct_metadata`)
- `required_evidence_refs`: array<evidence_id> (the evidence set used)
- `verification_outcome_notes`: string|null
- `human_approval_id`: string|null (present if human approval was required)

---

## 5) Timestamp fields (normative)

In addition to transition timestamps, each record must include:

### `record_created_time`
- **Type**: ISO-8601 timestamp string
- **Meaning**: when the record was created in the journal.

### `record_observed_time`
- **Type**: ISO-8601 timestamp string|null
- **Meaning**: time the underlying observation (if applicable) was obtained.

### `record_last_updated_time`
- **Type**: ISO-8601 timestamp string
- **Meaning**: when the record’s metadata/status header changed (metadata changes must themselves be logged as transitions or corrective events).

---

## 6) Access, sharing, and operational constraints

### `access_policy_id`
- **Type**: string
- **Purpose**: points to the applicable access policy contract (out of scope for this file, but must be referenced).

### `allowed_read_principals`
- **Type**: array<string> (optional if policy id fully determines)
- **Purpose**: explicit list of agent/role principals that may read operational view of this record.

### `allowed_write_principals`
- **Type**: array<string>
- **Purpose**: who may request future transitions for this record.

### `sharing_constraints`
- **Type**: object
- **Fields**:
  - `share_mode`: enum (`none`, `internal_only`, `role_only`, `project_only`, `limited_external`, `other`)
  - `share_until`: ISO-8601 timestamp string|null
  - `redaction_required`: boolean
  - `redaction_policy_id`: string|null

### `operational_use_permitted`
- **Type**: boolean
- **Purpose**: whether the record may be used to guide material actions in operational view.
- **Target rule**: typically `true` only for `verified` (and possibly narrow exceptions for `observed/asserted` under low-risk policy), but the contract requires policy gate enforcement.

---

## 7) Retention and deletion

### `retention_policy_id`
- **Type**: string
- **Purpose**: reference to retention rules applicable to this record category.

### `expiry_time`
- **Type**: ISO-8601 timestamp string|null
- **Purpose**: when the record (or operational use) expires.

### `legal_hold`
- **Type**: boolean
- **Purpose**: when true, retention/deletion requests must not remove evidence needed for audit.

### `retention_events`
- **Type**: array of `retention_event` objects
- **Purpose**: audit trace of expiry, deletion, redaction, or legal-hold overrides.

#### `retention_event` object (target)
- `event_id`: string
- `event_kind`: enum (`mark_expired`, `expire_operational_use`, `delete_record`, `redact_content`, `restore_visibility`, `apply_legal_hold`)
- `policy_gate_check_id`: string
- `timestamp_event`: ISO-8601 timestamp string
- `requested_by`: string
- `human_approval_id`: string|null

---

## Epistemic status semantics (target)

### `observed`
- Meaning: record reflects an observed event or input with minimal inference.
- Default trust posture: not yet a claim of meaning; operational use depends on policy and risk class.

### `asserted`
- Meaning: a producer claims interpretation/inference/recommendation beyond raw observation.
- Operational posture: can guide low-impact tasks only if policy allows, otherwise requires verification.

### `verified`
- Meaning: claim has met verification requirements for its risk class and domain according to policy.
- Operational posture: permitted for material decisions subject to access and recency constraints.

### `disputed`
- Meaning: conflict exists between records (or evidence) requiring explicit resolution.
- Operational posture: must not be treated as verified in operational view until resolved.

### `superseded`
- Meaning: a newer record replaces the prior interpretation for operational view (but does not destroy evidence).
- Operational posture: old record remains available in journal; operational use typically disabled or downgraded.

### `rejected`
- Meaning: verification failed, evidence was insufficient, authorization denied, or the claim was inconsistent with governing constraints.
- Operational posture: must not be used as verified.

### `unknown`
- Meaning: insufficient evidence to classify or resolve.
- Operational posture: must preserve uncertainty; operational use depends on policy and risk.

---

## Status transition rules (target)

### General rule: transitions require a policy-gated action
Every transition must include:
- a successful Policy Gate check (`policy_gate_check_id`);
- an append-only `transition_log` entry;
- evidence references used (or explicit “insufficient evidence” rationale if applicable);
- escalation logic when risk or dispute is high.

### Transition preconditions (normative matrix)

#### Observed flow
1. `unknown` → `observed`
   - Preconditions:
     - record has an observation source with evidence refs;
     - policy allows admission for the record domain/risk.
   - Allowed initiators: system for journaling, human input principals, or authorized tools.
   - Must not mark `verified`.

2. `observed` → `asserted`
   - Preconditions:
     - new claim content/interpretation is provided;
     - transition kind is `promote_asserted` and includes reasoning notes.
   - Allowed initiators: authorized agent roles or human principals.
   - Must preserve original observation source evidence links.

#### Assertion verification flow
3. `asserted` → `verified`
   - Preconditions:
     - verification process completed per policy for the claim_type and risk_class;
     - evidence refs include sufficient independent evidence per policy (independence group constraints apply).
   - Allowed initiators:
     - verification reviewers (not the same principal who performed the material claim certification).
   - Hard invariant enforcement:
     - **No self-certification**: if the agent made the claim, it may not alone certify it verified for operational use.

4. `asserted` → `disputed`
   - Preconditions:
     - contradiction or failure mode detected, or contested by allowed principal per dispute policy;
     - dispute thread created/linked.
   - Allowed initiators: reviewers, dispute-handling roles, or human.

5. `asserted` → `rejected`
   - Preconditions:
     - verification denied/failed; or policy gate refuses promotion due to risk/insufficient evidence.
   - Allowed initiators: policy-controlled rejection principal, reviewer roles, or human approval if required.

#### Dispute resolution flow
6. `disputed` → `verified`
   - Preconditions:
     - conflict resolution succeeded under policy, possibly producing an additional record that supersedes or resolves contradictions.
   - Evidence:
     - must reference the resolution evidence set explicitly.
   - Allowed initiators:
     - verification reviewer principal(s), with human approval if policy requires for critical/high domains.

7. `disputed` → `rejected`
   - Preconditions:
     - failure to resolve within policy evidence thresholds, or governance denies reliance.
   - Must preserve evidence and conflict records.

8. `disputed` → `unknown`
   - Preconditions:
     - evidence insufficient to resolve; must not pretend resolution.
   - Must preserve dispute thread and links for future revisit.

#### Supersession flow
9. `*` → `superseded`
   - Preconditions:
     - a newer operational interpretation exists and is policy-gated;
     - supersession links are set (`supersedes_record_id`) and contradiction mapping updated.
   - Evidence preservation:
     - primary evidence referenced by superseded record remains preserved; do not destroy evidence.
   - Allowed initiators:
     - policy-gated promotion principal(s); human if irreversible behavior is implicated by policy.

#### Expiry/deprecation flow
10. `verified` → `unknown` (or operational-use disabled) via `expire_operational_use`
   - Preconditions:
     - validity window expired or recency constraints breached.
   - Allowed initiators: system retention/expiry process under policy gate.

11. `verified/asserted/observed` → `rejected`
   - Preconditions:
     - later evidence discredits the claim beyond policy tolerance, or authorization withdrawn.
   - Must not delete the original evidence.

---

## Contradiction handling

### Representation
Contradictions must be explicit via:
- `contradicts_record_ids`
- `dispute_thread_id`
- transition kind `dispute`, `reclassify`, or `supersede` as appropriate

### Resolution constraints
- Resolution must produce an auditable path: either
  - promote the winning record to `verified`, or
  - reject/unknown the losing record(s), or
  - supersede with explicit reason and evidence.

### No silent replacement
Supersession must never silently overwrite original interpretations in a way that makes forensic reconstruction impossible.

---

## Admission rules (target)

### Admission categories
Admission occurs at least in these categories:
- `admit_observed`
- `promote_asserted`
- `verify`
- `supersede`
- `correct_metadata` (allowed only for provenance/metadata fixes, never to rewrite evidence truth without a logged correction transition)

### Evidence independence requirements
For `verified` transitions:
- “independent” must be defined via `source_independence_group` and policy rules.
- multiple claims drawn from the same underlying failure mode must not be treated as independent evidence without additional independence criteria.

---

## Access and sharing contract (target)

### Principle: sharing is permissioned, not implied
A record’s operational usefulness must not be shared beyond its `access_policy_id` and sharing_constraints.

### Sharing events must be auditable
When operational view permissions or sharing constraints change:
- a transition or retention event must be logged as an auditable change;
- policy gate check must be recorded.

> This contract treats access changes as material enough to require audit traceability.

---

## Human escalation requirements (target)

### Triggers for escalation
Escalation paths must exist (at minimum) when:
- a record is `critical` and remains `disputed` or unresolvable;
- verification would require crossing human-reserved authority boundaries;
- evidence conflicts cannot be resolved within allowed budget/cost thresholds by policy;
- a deletion/redaction request could harm forensic reconstruction (legal hold or high-impact evidence).

### Escalation logging
Every escalation must be represented as:
- an auditable transition or event record in the journal;
- including reason, policy gate check id, and whether human approval is required.

---

## Auditability and integrity requirements

### Append-only journal requirement
- Record creation and every status/retention transition must append to the journal.
- No “in-place mutation” of transition history.

### Forensic reconstruction requirement
Given a `record_id` and its `transition_log`, an authorized person must reconstruct:
- what was claimed;
- when/where it entered;
- provenance and evidence references;
- which verification steps occurred;
- why the final status was reached;
- what access/risk constraints governed its operational use.

### Tamper-evidence (optional but recommended)
- Implementations may add cryptographic chaining, signatures, or checksums.
- This contract does not require a specific cryptographic mechanism.

---

## Constraints on agents (target)

### Witnessing constraint
Any agent acting as a witness may:
- propose/submit claims and evidence references,
- request verification,
- but must not unilaterally mark its own claims as `verified` for material use.

### No governance rewriting
Agents must not modify access policies, verification policy thresholds, or status semantics without a logged, human-approved governance change procedure.

---

## Document structure for this contract (recommended outline)

This is the target layout for `MEMORY_LIFECYCLE_CONTRACT.md` itself:

1. Status banner (this file)
2. Purpose and scope
3. Hard invariants
4. Definitions
5. Record schema (normative fields)
6. Epistemic status semantics
7. Status transition rules (normative)
8. Contradiction handling
9. Admission rules
10. Access/sharing contract
11. Human escalation triggers
12. Auditability and integrity
13. Agent constraints
14. Change log (for future revisions)

---

## Open questions (tracked)

The following design questions are acknowledged as unresolved targets:

1. Exact mapping from `claim_type` to verification requirements (policy-driven matrix still TBD).
2. Whether “unknown” is a status vs an operational view override (this contract treats it as a status, but implementations may differ).
3. Identity continuity across upgrades and how memory attribution ties to identity migration (see `MIGRATION_PATH.md` as the target companion).
4. Precise independence modeling granularity (how many independence layers are required beyond `source_independence_group`).

---

## Change log (template)

- `2026-08-16`: Initial draft of contract structure and normative schema fields (TARGET proposal).
