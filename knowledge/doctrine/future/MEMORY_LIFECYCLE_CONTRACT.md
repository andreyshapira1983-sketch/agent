# STATUS: DRAFT / TARGET (not implemented)

# knowledge/doctrine/future/MEMORY_LIFECYCLE_CONTRACT.md

## Purpose

This contract defines the proposed lifecycle for long-horizon, per-agent durable memory in the future corporate model. It governs how a memory claim enters the journal, becomes eligible for retrieval, gains or loses operational standing, is challenged, is retained, and is removed from operational visibility.

A durable record is evidence-bearing history, not self-validating truth. Storage, producer confidence, role seniority, or repeated retrieval do not independently establish that a claim is verified.

This is a TARGET contract. It specifies intended future behavior only. It does not describe an implemented storage engine, access-control system, verifier, Policy Gate integration, or retention service.

The contract applies to durable records used by future agents, human principals, tools, imports, migrations, and governance processes. It does not govern transient model context unless that context is deliberately admitted as a durable record.

---

## Hard invariants this contract must preserve

| Invariant | Contract consequence |
|---|---|
| Policy Gate remains mandatory | A material admission, promotion, verification, sharing change, retention change, deletion request, retrieval for material action, or escalation takes effect only after a recorded successful Policy Gate evaluation. |
| Human-reserved authority remains in place | Human approval is required for governance changes, merge decisions, budget kill-switch decisions, and escalated irreversible or external actions. This contract does not create an exception to those boundaries. |
| Witnesses are not verified sources | An agent-produced claim enters as `observed` or `asserted`; it cannot become trusted operational memory merely because the producing agent or role considers it correct. |
| No self-certification | A principal that made a material claim, performed the underlying material action, or controlled the sole verification evidence cannot unilaterally transition that claim to `verified`. |
| Governance is not silently rewritten | Changes to status semantics, risk thresholds, access-policy interpretation, verification thresholds, retention classes, or escalation rules require a logged and human-approved governance change. |
| Primary evidence is preserved | Supersession, rejection, expiry, redaction, and deletion requests preserve an auditable reference to original primary evidence, its integrity metadata, and the reason access changed. |
| Disputes have an endpoint | High-impact or unresolved disputes enter an explicit dispute thread and escalate when defined evidence, time, or budget thresholds are exceeded. |
| Deep escalation is an event | Requests for high-cost or deep-model reasoning are reason-gated, budgeted, logged, and cannot be self-authorized by the requesting agent. |

---

## Definitions and scope

### Durable memory record

A durable memory record is an identifiable journal object containing a claim, its provenance, applicable controls, and append-only lifecycle events. It persists across individual model invocations and may be reconstructed by an authorized reviewer.

A record is not equivalent to the evidence it cites. Evidence may be a source artifact, tool response, human document, log segment, external observation, or another journal entry. A record may summarize or interpret evidence, but it must preserve the references needed to distinguish the interpretation from its sources.

### Claim vs fact

A claim is a proposition represented in a record. A fact is not a storage status. `verified` means the claim met the applicable verification policy at a particular time, for a stated scope and risk class. It does not mean the claim is universally, permanently, or metaphysically true.

### Material use

Material use is reliance on a record to authorize, select, execute, prioritize, suppress, approve, publish, spend, alter, merge, delete, share externally, or otherwise affect a consequential action.

Reading a record for exploration is not necessarily material use. Using that record as the basis for a high-impact recommendation or action is material use.

### Primary evidence

Primary evidence is the closest available record of the underlying observation or event: for example, an original tool response, source artifact, signed human document, source-system log, or preserved external capture. A summary, extraction, model interpretation, or derived memory record is not primary evidence when the underlying item is available.

### Independent evidence

Evidence is independent only when it does not rely on the same relevant failure mode as the claim it is intended to corroborate. Separate agents are not independent merely because they have different identifiers. Evidence is non-independent when it shares a source, observation pipeline, tool result, model-generated interpretation, privileged operator, dataset, or material assumption unless policy explicitly establishes otherwise.

### Views

| View | Contents | Permitted mutation |
|---|---|---|
| Chronological evidence journal | Record creation events, evidence references, status transitions, access changes, retention events, dispute links, approvals, and policy evaluations. | Append-only additions and explicitly logged corrective events. |
| Current operational view | The presently usable representation of records after status, scope, freshness, risk, access, retention, and dispute controls are applied. | Derived projection only; it may change when the journal receives a new event. |

The current operational view must never erase the journal history that explains why a record is absent, restricted, expired, disputed, superseded, rejected, or redacted.

---

## Record schema (target)

### Storage model assumption (target)

Each record has a stable identity and immutable creation payload. Later changes are represented by journal events that update the derived operational view rather than silently replacing history.

A conforming record contains the following logical groups:

| Group | Required contents |
|---|---|
| Record Identity | `record_id` and linkage fields. |
| Header | Producer identity, claim type, domain, subject, risk, and scope. |
| Provenance | Observation sources, evidence references, observation time, and transformation lineage. |
| Epistemic Content | A canonical claim statement or structured claim representation. |
| Status and Transitions | Current status projection and append-only transition log. |
| Access and Risk | Access policy, sharing restrictions, operational-use restrictions, and retrieval audit requirements. |
| Retention | Retention policy, expiry, legal-hold state, and retention-event history. |

A record is structurally invalid unless all required fields are present, all referenced identifiers are syntactically valid, and at least one of `claim_text` or `claim_struct` is present.

---

## 1) Identity and linkage fields

### `record_id`

| Property | Value |
|---|---|
| Type | string |
| Format | Opaque stable identifier, such as a UUIDv7-like identifier. |
| Rule | Generated once at admission and never reused for another record. |
| Meaning | Identifies one journaled record instance, not an abstract claim and not an evidence hash. |

### `parent_record_id` (optional)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | References the immediate source record when this record is derived by summarization, extraction, translation, aggregation, normalization, or migration. |
| Meaning | Establishes lineage without making the child record inherit the parent’s epistemic status. |

### `supersedes_record_id` (optional, nullable)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Set only when this record replaces the referenced record for a defined operational scope. A superseding record must identify the scope and reason in its transition log. |
| Meaning | Links an operational replacement; it does not destroy or invalidate the superseded record’s evidence. |

### `contradicts_record_ids` (list, possibly empty)

| Property | Value |
|---|---|
| Type | array<string> |
| Rule | Each listed record must have a reciprocal contradiction link or a logged reason why reciprocal linking is impossible. |
| Meaning | Identifies claims that cannot both be relied on within the same applicability window and decision scope. |

### `dispute_thread_id` (optional)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Required when status is `disputed`; retained when a dispute later resolves so that the resolution path remains reconstructible. |
| Meaning | Groups related conflicting records, evidence, resolution attempts, budget decisions, and escalation events. |

---

## 2) Producer and provenance

### `producer_agent_id`

| Property | Value |
|---|---|
| Type | string |
| Rule | Names the creating principal, tool principal, human principal, import principal, or migration principal. |
| Meaning | Attributes creation of this record instance; it does not certify the claim’s truth. |

### `producer_role` (optional)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Records the role or contract identity active at creation time. Historical role text is preserved even if the role later changes. |
| Meaning | Supports accountability and policy evaluation. |

### `producer_method`

| Property | Value |
|---|---|
| Type | enum |
| Allowed values | `agent_output`, `tool_output`, `human_input`, `imported`, `migration_inheritance` |
| Rule | Exactly one value is required. Imported and migrated records must identify the source system or predecessor record in `provenance_notes` or `evidence_refs`. |

### `observation_source`

| Field | Type | Required rule |
|---|---|---|
| `source_kind` | enum | One of `log`, `artifact`, `external_observation`, `dataset`, `tool_call`, `human_document`, `memory_journal_entry`, `other`. |
| `source_refs` | array<`evidence_ref`> | At least one reference for `observed` records; may be empty only for a clearly marked `unknown` record created to track an unresolved question. |
| `source_independence_group` | string \| null | Required for evidence offered toward `verified`; equal values indicate a shared failure mode unless policy records a narrower distinction. |

### `evidence_refs`

| Property | Value |
|---|---|
| Type | array<`evidence_ref`> |
| Rule | Every record must contain evidence references, except an `unknown` record tracking an unanswered question. The absence of evidence is itself recorded as a reason, not implied. |
| Rule for derived content | A transformed or aggregated claim must cite both its direct input records and, where permitted, the primary evidence links from those inputs. |

#### `evidence_ref` object (target)

| Field | Type | Allowed values or rule |
|---|---|---|
| `evidence_id` | string | Stable identifier for the evidence item or immutable journal event. |
| `evidence_kind` | enum | `tool_response`, `prompt_log`, `artifact_checksum`, `external_link`, `human_note`, `source_log`, `dataset_snapshot`, `signed_document`, `other`. |
| `retrieval_policy_id` | string \| null | Names the policy governing re-access, reproduction, or redaction of the evidence item. |
| `integrity_fingerprint` | string \| null | Hash, signature reference, or immutable version identifier when available. A missing fingerprint must be explained for evidence used in high or critical verification. |
| `created_from` | enum | `direct`, `transformed`, or `aggregated`. |
| `time_of_observation` | ISO-8601 timestamp string \| null | Time the evidence was observed or captured, distinct from record creation time. |

### `provenance_notes`

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Contains concise audit-relevant context not represented structurally, including known limitations, missing source material, import conditions, or transformation warnings. |
| Prohibited use | It must not be used to conceal a required evidence link, approval reference, source identity, or policy decision. |

---

## 3) Claim content

### `claim_type`

| Property | Value |
|---|---|
| Type | enum |
| Allowed values | `factual_observation`, `causal_inference`, `recommendation`, `policy_interpretation`, `decision_commitment`, `operational_state`, `identity_assertion`, `obligation_claim` |
| Rule | The selected type determines the verification policy class. A record with multiple materially different claim types is split into separate records linked by lineage or dispute links. |

### `claim_domain`

| Property | Value |
|---|---|
| Type | string |
| Rule | Uses a governed domain label such as `finance`, `operations`, `research`, `governance`, `identity`, `technical_architecture`, or another policy-recognized domain. |
| Meaning | Identifies the policy family, subject-matter owner, and retention category used for evaluation. |

### `claim_subject`

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Identifies the entity, component, project, obligation, person, agent, or external object to which the claim applies when such identification is permitted. |
| Privacy rule | Sensitive subjects are represented using the access policy’s approved identifier form. |

### `claim_text` (optional if claim is structured)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | States the canonical human-readable proposition in language that distinguishes observation from inference. |
| Example | “The deployment log reports version X at time T” is an observation; “Version X caused incident Y” is a causal inference and requires separate support. |

### `claim_struct` (optional)

| Property | Value |
|---|---|
| Type | object \| null |
| Rule | Contains machine-evaluable predicates, values, units, identifiers, confidence inputs, or comparison operators when needed. |
| Consistency rule | If both `claim_text` and `claim_struct` are present, a verification or metadata-correction event must resolve any material inconsistency between them. |

### `applicability_window`

| Field | Type | Rule |
|---|---|---|
| `valid_from` | ISO-8601 timestamp string \| null | Start of the period during which the claim is intended to apply. |
| `valid_to` | ISO-8601 timestamp string \| null | End of intended applicability. A null value does not remove freshness review requirements. |
| `scope` | enum | One of `global`, `agent_scoped`, `role_scoped`, `project_scoped`, `time_scoped`, `other`. |
| `scope_ref` | string \| null | Required for every scope other than `global`; identifies the agent, role, project, period, or defined scope. |

### `risk_class`

| Property | Value |
|---|---|
| Type | enum |
| Allowed values | `low`, `medium`, `high`, `critical` |
| Assignment rule | Assigned at admission from claim type, domain, reversibility, blast radius, sensitivity, and cost of error. |
| Reclassification rule | A later risk increase creates a `reclassify` transition; it does not silently preserve earlier operational permissions. |

---

## 4) Epistemic status and transition trace

### `epistemic_status`

| Value | Operational meaning |
|---|---|
| `observed` | A source event or input was recorded with minimal interpretation. |
| `asserted` | A principal has expressed an interpretation, inference, recommendation, or commitment. |
| `verified` | A non-self-certifying verification process satisfied the policy for the claim’s risk, domain, scope, and freshness. |
| `disputed` | Material conflicting evidence, claims, or policy interpretations require explicit resolution. |
| `superseded` | A later record replaces this record for specified operational use while preserving its historical evidence and status history. |
| `rejected` | The claim failed verification, was denied by policy, was materially discredited, or lacks authorization needed for reliance. |
| `unknown` | Evidence is insufficient to classify or resolve the proposition. |

### `status_effective_time`

| Property | Value |
|---|---|
| Type | ISO-8601 timestamp string |
| Rule | Equals the `timestamp_effective` of the latest effective status transition. |
| Meaning | Marks when the operational view began applying the current status. |

### `status_reason`

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Explains the current status in reviewer-readable language and cites the controlling transition identifier. |
| Constraint | It summarizes; it does not replace the evidence references or transition log. |

### `verification_request_id` (optional)

| Property | Value |
|---|---|
| Type | string \| null |
| Rule | Required for a `verified` status unless the verification was performed as part of a human-approved governance or emergency process identified in the transition notes. |
| Meaning | Links the claim to the request, reviewer assignment, evidence criteria, and outcome. |

### `transition_log`

| Property | Value |
|---|---|
| Type | array<`status_transition`> |
| Rule | Contains the admission event and every later effective status or metadata transition in chronological order. |
| Immutability rule | Existing entries are never edited or removed. A correction is a new `correct_metadata` event referencing the event corrected. |

#### `status_transition` object (target)

| Field | Type | Rule |
|---|---|---|
| `transition_id` | string | Stable event identifier. |
| `from_status` | enum | Status before the event; admission uses `unknown` as the conceptual source state. |
| `to_status` | enum | Status after the event. |
| `initiator_principal` | string | Principal requesting the transition. |
| `initiator_role` | string \| null | Role active when requested. |
| `policy_gate_check_id` | string | Required reference to the successful policy evaluation. |
| `timestamp_requested` | ISO-8601 timestamp string | Time the request entered the journal. |
| `timestamp_effective` | ISO-8601 timestamp string | Time the policy-authorized transition affected the operational view. |
| `transition_kind` | enum | `admit_observed`, `promote_asserted`, `verify`, `dispute`, `supersede`, `reject`, `expire`, `reclassify`, `correct_metadata`. |
| `required_evidence_refs` | array<evidence_id> | Evidence considered for the transition; an empty list requires an explicit insufficiency or policy rationale. |
| `verification_outcome_notes` | string \| null | Findings, limitations, independence analysis, and unresolved uncertainty. |
| `human_approval_id` | string \| null | Required whenever the applicable policy or human-reserved authority boundary requires approval. |

---

## 5) Timestamp fields (normative)

### `record_created_time`

| Property | Value |
|---|---|
| Type | ISO-8601 timestamp string |
| Meaning | Time the record creation event was appended to the journal. |
| Rule | Immutable after admission. |

### `record_observed_time`

| Property | Value |
|---|---|
| Type | ISO-8601 timestamp string \| null |
| Meaning | Time the underlying event or input was observed. |
| Rule | Must not be substituted with record creation time when the observation time is unknown; unknown remains null with an explanatory provenance note. |

### `record_last_updated_time`

| Property | Value |
|---|---|
| Type | ISO-8601 timestamp string |
| Meaning | Time the latest journal event changed the derived record header, status, access state, or retention state. |
| Rule | Must equal the latest applicable transition or retention event time. |

All timestamps use ISO-8601 with an explicit UTC offset. Policy evaluation records retain their own evaluation time and policy-version reference.

---

## 6) Access, sharing, and operational constraints

### `access_policy_id`

| Property | Value |
|---|---|
| Type | string |
| Rule | References the governing access policy version evaluated for read, write-request, retrieval, sharing, redaction, and evidence access. |
| Default posture | If the policy cannot be resolved, operational retrieval is denied and the record remains journaled. |

### `allowed_read_principals`

| Property | Value |
|---|---|
| Type | array<string> |
| Rule | May narrow but never widen the rights granted by `access_policy_id`. |
| Default posture | An omitted list means the policy determines readers; it does not mean public or unrestricted access. |

### `allowed_write_principals`

| Property | Value |
|---|---|
| Type | array<string> |
| Rule | Names principals permitted to request transitions. It does not grant unilateral authority to make a requested transition effective. |
| Constraint | No entry overrides reviewer separation, human approval, Policy Gate, or no-self-certification requirements. |

### `sharing_constraints`

| Field | Type | Rule |
|---|---|---|
| `share_mode` | enum | `none`, `internal_only`, `role_only`, `project_only`, `limited_external`, or `other`. |
| `share_until` | ISO-8601 timestamp string \| null | Latest time sharing is allowed. A null value remains subject to policy revocation and expiry. |
| `redaction_required` | boolean | Requires policy-approved redaction before the record or referenced content is shared. |
| `redaction_policy_id` | string \| null | Required when `redaction_required` is `true`. |

### `operational_use_permitted`

| Property | Value |
|---|---|
| Type | boolean |
| `true` condition | The record is in scope, fresh enough, accessible to the requesting principal, not blocked by legal hold or sharing restrictions, and its status/risk combination is authorized by a recorded policy evaluation. |
| `false` condition | Any denial condition applies, including `disputed`, `rejected`, expired use, unresolved access policy, missing required verification, or a superseded operational scope. |
| Default | `false` for `critical` claims until verified. `observed` and `asserted` records are not materially actionable unless an explicit low-risk, reversible policy permits the narrow use. |

### Retrieval decision rules

| Retrieval request | Decision |
|---|---|
| Exploratory read of an accessible `observed`, `asserted`, or `unknown` record | May return the record only with its status, provenance limitations, and operational-use restriction visible to the reader. |
| Retrieval for material action | Requires a current Policy Gate evaluation, a permitted access path, a matching applicability window, and a status authorized for the relevant risk class. |
| Retrieval of `disputed` record | Returns the dispute marker, conflicting record identifiers, and escalation state. It must not be presented as verified support. |
| Retrieval of `superseded` record | Returns historical context only unless policy explicitly permits comparison or forensic review. The superseding record and supersession reason are included where access allows. |
| Retrieval of `rejected` record | Limited to audit, verification, dispute resolution, or policy-authorized learning contexts; it cannot support a material conclusion as verified evidence. |
| Retrieval after `expiry_time` | Denies operational use unless a new policy-gated review renews applicability. |
| Retrieval blocked by access policy | Returns no content beyond the minimum denial metadata permitted by policy. |
| Retrieval requiring deep or high-cost analysis | Creates a reason-gated, budgeted escalation request. The requesting agent cannot approve its own escalation. |

Every material retrieval decision is journaled with requesting principal, purpose, record identifiers, policy decision reference, time, and whether the returned record was eligible for operational use.

---

## 7) Retention and deletion

### `retention_policy_id`

| Property | Value |
|---|---|
| Type | string |
| Rule | References the retention schedule for the record’s domain, risk, evidence sensitivity, legal obligations, and audit needs. |
| Precedence | A legal hold, active dispute, active investigation, or policy-required preservation period overrides ordinary expiry or deletion scheduling. |

### `expiry_time`

| Property | Value |
|---|---|
| Type | ISO-8601 timestamp string \| null |
| Meaning | Time after which operational use is disabled unless renewed through a logged policy-gated review. |
| Constraint | Expiry does not erase the journal record or primary evidence reference. |

### `legal_hold`

| Property | Value |
|---|---|
| Type | boolean |
| `true` effect | Blocks deletion, destructive redaction, evidence disposal, and retention shortening until an authorized human-controlled hold-release process records otherwise. |
| `false` effect | Does not itself authorize deletion; ordinary retention and approval rules still apply. |

### `retention_events`

| Property | Value |
|---|---|
| Type | array<`retention_event`> |
| Rule | Contains every expiry, visibility restoration, legal-hold application, redaction, and deletion action affecting the record or accessible evidence. |

#### `retention_event` object (target)

| Field | Type | Rule |
|---|---|---|
| `event_id` | string | Stable retention-event identifier. |
| `event_kind` | enum | `mark_expired`, `expire_operational_use`, `delete_record`, `redact_content`, `restore_visibility`, `apply_legal_hold`. |
| `policy_gate_check_id` | string | Required successful policy evaluation reference. |
| `timestamp_event` | ISO-8601 timestamp string | Effective event time. |
| `requested_by` | string | Requesting principal. |
| `human_approval_id` | string \| null | Required for destructive deletion, legal-hold release through its governing process, and any action crossing a human-reserved authority boundary. |

### Retention outcomes

| Condition | Required outcome |
|---|---|
| Ordinary expiry reached | Append `mark_expired` and `expire_operational_use`; preserve the record and evidence references. |
| Record remains needed for active dispute, audit, investigation, or legal hold | Retain journal history and primary evidence references regardless of ordinary expiry. |
| Sensitive content must no longer be visible to a class of readers | Append `redact_content` or access-policy event; preserve a restricted audit manifest describing what changed, why, and under which authority. |
| Destructive deletion is requested | Require Policy Gate approval, retention-policy authorization, confirmation that no hold or preservation obligation applies, and human approval. Append a deletion event and preserve a non-content tombstone with record identity, integrity metadata where permitted, authority, and reason. |
| Later policy permits restored access | Append `restore_visibility`; do not imply that the prior restriction was erroneous. |
| Legal hold is applied | Append `apply_legal_hold`, disable destructive retention actions, and notify the dispute/audit path identified by policy. |

Deletion removes content only to the extent authorized by retention and privacy policy. It never permits silent deletion of the journal event that records deletion or destruction of the evidence chain.

---

## Epistemic status semantics (target)

### `observed`

An `observed` record states that an input, event, or source was captured. It contains minimal interpretation and identifies the source and observation time where known.

An observed record may support investigation, comparison, or low-risk reversible work if policy allows. It is not by default a verified explanation, recommendation, authorization, or causal conclusion.

### `asserted`

An `asserted` record contains an inference, interpretation, recommendation, policy reading, commitment, or other proposition beyond direct observation.

The producer may request verification, dispute another record, or submit additional evidence. The producer cannot use its own assertion as sole certification for material action.

### `verified`

A `verified` record has satisfied the applicable evidence, reviewer-separation, independence, risk, scope, and freshness policy. The verification outcome identifies what was verified, the evidence considered, known limitations, and the policy version applied.

Verification is bounded. A verified record may later become disputed, rejected, superseded, expired for operational use, or reclassified when its evidence, scope, policy, or risk context changes.

### `disputed`

A `disputed` record has a material unresolved conflict with evidence, another record, a policy interpretation, or a properly raised challenge. It is linked to a dispute thread and cannot be represented as verified support for material action.

A dispute may concern truth, applicability, provenance, authorization, freshness, independence, or interpretation. These grounds are recorded in the transition notes.

### `superseded`

A `superseded` record remains historically valid as a record of what was previously observed, asserted, or verified. It is replaced only for the specified operational scope by an identified later record.

Supersession does not necessarily mean the earlier claim was false. It may reflect newer evidence, a narrower scope, changed conditions, improved precision, or a revised policy interpretation.

### `rejected`

A `rejected` record has failed the required verification path, been materially discredited, lacked required authorization, or been denied by policy. Its evidence and reasoning remain available to authorized audit and dispute processes.

Rejected records cannot be retrieved as verified support. They may be examined to prevent repeated error, assess source reliability, or resolve a related dispute.

### `unknown`

An `unknown` record preserves a proposition whose evidence is incomplete, inaccessible, ambiguous, or unresolved. It records uncertainty rather than manufacturing confidence.

An unknown record is not a neutral substitute for verification. It may identify a research task or risk condition, but cannot support material action unless a narrowly defined policy allows a reversible low-risk response.

---

## Status transition rules (target)

### General rule: transitions require a policy-gated action

A status transition takes effect only when all of the following are recorded:

1. the requested transition and requesting principal;
2. the applicable policy version and successful `policy_gate_check_id`;
3. the evidence set considered, including any insufficiency finding;
4. separation between claimant, actor, reviewer, and approver where policy requires it;
5. required human approval reference;
6. the effective time, status reason, and operational-use consequence;
7. dispute and escalation references when the transition involves contradiction, high risk, or unresolved uncertainty.

A policy denial is also journaled. It does not change the requested status, but records the denied request, reason, evidence considered, and available escalation path.

### Transition preconditions (normative matrix)

| From | To | Transition kind | Preconditions | Who may request | Who may make effective | Required outcome |
|---|---|---|---|---|---|---|
| `unknown` | `observed` | `admit_observed` | Source exists; evidence reference is attached; claim is framed as observation; risk and access are assigned. | Authorized system, tool, human, import, or agent principal. | Policy-authorized admission process. | Record is visible only according to access policy and is not marked verified. |
| `observed` | `asserted` | `promote_asserted` | Interpretation or recommendation is explicitly stated; reasoning and source links are attached; original observation remains linked. | Authorized agent role or human principal. | Policy-authorized promotion process. | New inference is distinguishable from source observation. |
| `unknown` | `asserted` | `promote_asserted` | The record tracks an unresolved proposition; a producer supplies a claim and explains missing or incomplete evidence. | Authorized agent role or human principal. | Policy-authorized promotion process. | Operational restrictions reflect unresolved evidence. |
| `asserted` | `verified` | `verify` | Verification criteria for claim type, domain, and risk are met; evidence independence is assessed; reviewer separation is satisfied. | Assigned reviewer or authorized human principal. | Independent verification authority under policy. | Verification request, evidence, limitations, and reviewer identity are journaled. |
| `observed` | `verified` | `verify` | Policy explicitly permits direct verification of an observational claim; verification remains independent of the producer where material. | Assigned reviewer or authorized human principal. | Independent verification authority under policy. | Direct observation and verification findings remain separately identifiable. |
| `asserted` | `disputed` | `dispute` | Material contradiction, challenge, provenance concern, independence failure, or policy conflict is identified. | Authorized reviewer, dispute role, affected principal, or human. | Policy-authorized dispute process. | A dispute thread and contradiction links are created. |
| `verified` | `disputed` | `dispute` | Later evidence, scope conflict, freshness failure, or credible challenge undermines the prior verification basis. | Authorized reviewer, dispute role, or human. | Policy-authorized dispute process. | Material operational reliance is suspended or narrowed according to policy. |
| `observed` | `disputed` | `dispute` | The authenticity, provenance, or interpretation of the observation is materially challenged. | Authorized reviewer, dispute role, or human. | Policy-authorized dispute process. | Record remains preserved with a clear dispute marker. |
| `disputed` | `verified` | `verify` | The dispute resolution evidence meets the applicable policy threshold; all material conflicts are addressed or narrowly scoped. | Assigned independent reviewer or human principal. | Independent verification authority; human approval for high or critical cases when policy requires. | Resolution cites the dispute thread, evidence, and residual uncertainty. |
| `disputed` | `rejected` | `reject` | Evidence disproves the claim, governing authority denies reliance, or conflict resolution establishes that the claim cannot be used. | Reviewer, dispute authority, or human. | Policy-authorized rejection authority. | Contradictory records and primary evidence remain preserved. |
| `disputed` | `unknown` | `reclassify` | Available evidence cannot resolve the conflict within policy thresholds, budget, or time limits. | Reviewer, dispute authority, or human. | Policy-authorized dispute process. | Dispute thread remains open or is marked paused with explicit revisit conditions. |
| `observed` | `rejected` | `reject` | Provenance is materially invalid, observation is fabricated or corrupted, or policy forbids reliance. | Reviewer, policy authority, or human. | Policy-authorized rejection authority. | Original source reference and rejection rationale remain journaled. |
| `asserted` | `rejected` | `reject` | Verification fails, evidence is inadequate for permitted use, or authorization is denied. | Reviewer, policy authority, or human. | Policy-authorized rejection authority. | The record is unavailable as verified support. |
| `verified` | `rejected` | `reject` | Later evidence discredits the claim beyond policy tolerance or required authorization is withdrawn. | Independent reviewer, dispute authority, or human. | Policy-authorized rejection authority; human approval for critical consequences. | Prior verification remains historical; current operational use is disabled. |
| `observed`, `asserted`, `verified`, `disputed`, or `unknown` | `superseded` | `supersede` | A newer scoped record is identified; supersession reason, scope, and evidence are recorded; no evidence is overwritten. | Authorized reviewer, curator role, or human. | Policy-authorized supersession authority; human approval where policy treats the operational consequence as irreversible or high impact. | `supersedes_record_id` is populated on the successor and the predecessor’s operational view is narrowed. |
| `verified` | `unknown` | `expire` | Validity window or freshness threshold has elapsed and no renewal verification exists. | Authorized retention process or reviewer. | Policy-authorized expiry process. | Operational use is disabled while evidence remains preserved. |
| Any status | same status | `correct_metadata` | Correction concerns non-substantive metadata, a malformed reference, or a routing/access annotation; the correction cannot alter the historical claim or evidence content. | Authorized curator, reviewer, or human. | Policy-authorized correction process. | New corrective event identifies the prior value, replacement value, reason, and affected fields. |
| Any nonterminal status | `unknown` | `reclassify` | Classification cannot be maintained because scope, evidence availability, policy interpretation, or risk basis is insufficient. | Reviewer, dispute authority, or human. | Policy-authorized reclassification authority. | Uncertainty and review conditions are explicit. |

A transition not listed in this matrix is denied unless a human-approved governance change adds it and records the policy version that authorizes it.

---

## Contradiction handling

### Representation

A contradiction exists when two records cannot both be relied on for the same decision-relevant proposition, scope, and applicability window. Mere difference in wording, granularity, or time period is not a contradiction when both claims can be true within their stated scopes.

Each material contradiction creates or joins a `dispute_thread_id` and records:

| Required dispute element | Journal treatment |
|---|---|
| Conflicting record identifiers | Added to `contradicts_record_ids` with reciprocal linkage where possible. |
| Proposition in conflict | Stated in reviewer-readable language, including scope and time window. |
| Conflict type | Classified as evidence conflict, provenance conflict, interpretation conflict, authorization conflict, freshness conflict, or policy conflict. |
| Immediate operational consequence | Specifies whether use is suspended, narrowed, segregated by scope, or remains allowed under an explicit low-risk exception. |
| Resolution criteria | Identifies evidence threshold, required reviewer separation, deadline, budget limit, and human escalation threshold. |
| Resolution result | Records verified winner, rejected claim, unknown outcome, scoped coexistence, or superseding record. |

### Resolution constraints

A dispute resolves only through one of these outcomes:

1. **Verified resolution:** an independently reviewed record becomes `verified` for the disputed scope and cites the resolution evidence.
2. **Scoped coexistence:** records remain valid only because their applicability windows or subjects are explicitly separated.
3. **Rejection:** one or more claims become `rejected` with preserved evidence and rationale.
4. **Uncertainty preservation:** contested claims become or remain `unknown` because policy evidence thresholds were not met.
5. **Supersession:** a later record replaces prior operational interpretations while preserving the dispute thread and prior evidence.

A dispute involving a `critical` record, an identity assertion, governance interpretation, legal hold, external consequence, or human-reserved authority cannot be closed solely by the claimant or by a principal sharing the claimant’s sole evidence source.

### No silent replacement

Supersession, reclassification, rejection, and metadata correction do not overwrite the content or transition history of the earlier record. The operational view may stop presenting an earlier claim as current, but authorized forensic reconstruction must still reveal:

- the earlier claim;
- its prior status and evidence;
- the record or event that changed its operational standing;
- the scope of the change;
- the policy decision and human approval, if any.

---

## Admission rules (target)

### Admission phases

| Phase | Entry condition | Required action | Exit condition |
|---|---|---|---|
| 1. Intake | A principal proposes a durable record, import, observation, or unresolved question. | Assign `record_id`, producer attribution, claim domain, initial risk class, access policy, and retention policy. | Structural validation succeeds or the intake is rejected without creating trusted memory. |
| 2. Provenance binding | The intake has claim content. | Attach source references, observation time, transformation lineage, integrity metadata where available, and independence group. | Evidence is sufficient for the intended initial status or insufficiency is explicitly recorded. |
| 3. Policy admission | Required fields and provenance are present. | Run Policy Gate for admission, access scope, retention category, and initial operational-use restriction. | A successful decision authorizes journal admission; a denial remains an auditable rejected request. |
| 4. Initial classification | Admission is authorized. | Create an `observed`, `asserted`, or `unknown` record; never create an agent-produced material claim directly as `verified`. | Initial transition is appended and current operational view is derived. |
| 5. Verification or dispute | A claim needs trusted use, has sufficient evidence, or conflicts with another record. | Request independent review, gather permitted evidence, test independence, or open a dispute thread. | The record becomes `verified`, `disputed`, `rejected`, remains `asserted`, or becomes `unknown`. |
| 6. Active use and review | Record is retrievable in operational view. | Apply access, freshness, scope, risk, and material-use checks at retrieval. | Record remains eligible, is renewed, is disputed, superseded, expired, or rejected. |
| 7. Retention closure | Expiry, retention schedule, legal hold, redaction requirement, or deletion request occurs. | Preserve required journal history and evidence chain; apply visibility or deletion action only under policy and approval rules. | Record is retained, operationally expired, redacted, tombstoned, or restored. |

### Admission categories

| Category | Initial or resulting status | Required evidence posture |
|---|---|---|
| `admit_observed` | `observed` | Source event or input reference is required. |
| `promote_asserted` | `asserted` | Claim reasoning and links to supporting or motivating evidence are required. |
| `verify` | `verified` | Risk-appropriate verification evidence, independence assessment, reviewer separation, and policy authorization are required. |
| `supersede` | predecessor becomes `superseded` | A successor record, supersession scope, reason, and preserved evidence links are required. |
| `correct_metadata` | status unchanged | Prior value, corrected value, reason, and confirmation that claim/evidence history was not rewritten are required. |

### Evidence independence requirements

| Risk class | Minimum verification posture for `verified` status |
|---|---|
| `low` | Evidence relevant to the claim and a policy-authorized review path; a single direct primary source may suffice only for reversible, limited-scope use. |
| `medium` | At least one primary or authoritative source plus independent review of claim interpretation and applicability. |
| `high` | Multiple policy-accepted evidence paths or a documented reason why independent corroboration is unavailable; reviewer must be independent of claimant and material actor. |
| `critical` | Independent evidence paths, explicit failure-mode analysis, human escalation where policy requires, and no unresolved material contradiction. A lack of independence results in `unknown`, `disputed`, or `rejected`, not verification. |

Evidence with the same `source_independence_group` counts as one corroborating path unless the verification record documents a policy-approved reason to treat components separately.

---

## Access and sharing contract (target)

### Principle: sharing is permissioned, not implied

A record belongs to the access scope granted by its policy, not to every agent that might benefit from reading it. Sharing follows least privilege, purpose limitation, retention limits, and redaction requirements.

Possession of a record identifier, parent link, dispute-thread identifier, or evidence identifier does not independently grant access to record content.

### Sharing decision rules

| Requested sharing action | Required decision |
|---|---|
| Share within the same authorized project scope | Confirm reader principal, purpose, expiry, and redaction status through policy before release. |
| Share across role or agent boundaries | Require a policy-gated access decision and journal the sharing event when the record is sensitive, material, or newly exposed. |
| Share externally | Requires explicit human approval unless a separately approved, reversible policy exception narrowly permits the action. External sharing includes derived summaries that reveal protected claim content. |
| Share a disputed, rejected, or unknown record | Include status, uncertainty, and use restrictions. The receiving principal must not receive it as verified support. |
| Share evidence requiring redaction | Redact according to `redaction_policy_id`, preserve a restricted audit reference to the original, and journal the redaction event. |
| Change reader or writer permissions | Treat as a material access change: run Policy Gate, append an auditable event, and retain the prior policy reference. |

### Sharing events must be auditable

Every material access or sharing change records:

- record identifier and affected evidence identifiers;
- requesting and receiving principals;
- stated purpose and permitted use;
- policy version and `policy_gate_check_id`;
- redaction policy and result where applicable;
- effective time and expiry;
- human approval reference where required.

A sharing event changes visibility only. It does not upgrade epistemic status or operational-use permission.

---

## Human escalation requirements (target)

### Triggers for escalation

| Trigger | Immediate system posture | Escalation recipient |
|---|---|---|
| A `critical` record remains `disputed` after the policy-defined review interval | Disable material operational use and preserve all conflict evidence. | Human authority identified by the applicable domain policy. |
| Verification would authorize an irreversible, external, governance, merge, or budget-kill-switch consequence | Do not execute or certify the consequence from memory status alone. | Human-reserved authority. |
| A high or critical claim lacks independent evidence but continued reliance is requested | Keep the claim `unknown`, `asserted`, or `disputed`; do not promote by confidence. | Human authority or designated independent review path. |
| Conflict resolution exceeds the permitted cost, time, or evidence-acquisition budget | Stop further escalation work unless a new budget decision is approved. | Human budget authority. |
| Deletion, redaction, or retention shortening could impair legal hold, active dispute, audit, or forensic reconstruction | Block destructive action pending review. | Human retention, legal, or governance authority defined by policy. |
| A request requires deep-model or high-cost reasoning | Create a reason-gated escalation event containing expected value, budget, alternatives considered, and requested scope. | Separate budget-authorizing principal; never the requesting agent alone. |
| A record concerns identity continuity, authority inheritance, or governance interpretation | Suspend use outside the narrowest safe scope. | Human governance authority. |

### Escalation logging

Each escalation is represented by a journal event or linked transition containing:

| Field | Required value |
|---|---|
| `escalation_id` | Stable identifier. |
| `record_id` or `dispute_thread_id` | Target record or dispute. |
| `reason_code` | `critical_dispute`, `human_reserved_authority`, `budget_limit`, `evidence_insufficiency`, `retention_risk`, `deep_review_request`, `governance_question`, or policy-defined equivalent. |
| `policy_gate_check_id` | Policy decision that required or authorized escalation. |
| `requested_by` | Principal requesting escalation. |
| `requested_scope` | Exact question, action boundary, evidence set, or retention decision requiring review. |
| `budget_request` | Cost ceiling and funding authority when deep review is requested. |
| `human_approval_id` | Required when approval has been granted. |
| `resolution` | Approved, denied, deferred, narrowed, or unresolved, with reasons and follow-up conditions. |

Escalation approval authorizes only the stated scope. It does not create standing authority for future escalations, verification shortcuts, governance changes, or unrestricted use of the affected record.

---

## Auditability and integrity requirements

### Append-only journal requirement

The journal contains an immutable sequence of:

- record-admission events;
- status transitions;
- verification requests and outcomes;
- contradiction and dispute events;
- retrieval events for material use;
- access and sharing changes;
- retention, redaction, legal-hold, and deletion events;
- escalation requests, budget decisions, and human approvals;
- corrective events and governance-version references.

The current operational view is derived from this sequence. A repair to malformed metadata is recorded as a new corrective event; it is not an in-place edit to prior history.

### Forensic reconstruction requirement

Given a `record_id`, an authorized reviewer must be able to reconstruct:

| Question | Required reconstruction source |
|---|---|
| What was claimed? | Immutable admission payload, `claim_text`, `claim_struct`, and corrective-event history. |
| Who created it and under what role? | Producer fields, role attribution, and creation event. |
| What evidence supported it? | `observation_source`, `evidence_refs`, integrity metadata, and transformation lineage. |
| When did the underlying event and journal admission occur? | Observation, creation, and transition timestamps. |
| Why did its status change? | Ordered transition log, evidence references, policy decisions, reviewer notes, and approvals. |
| Was it eligible for material use at a given time? | Status effective time, risk class, applicability window, access policy, retention state, freshness decision, and retrieval audit. |
| Was there a dispute, supersession, or deletion action? | Contradiction links, dispute thread, supersession links, retention events, tombstones, and escalation records. |

### Tamper-evidence (optional but recommended)

A future implementation may use cryptographic hashes, signatures, append-only sequence numbers, Merkle structures, trusted timestamps, or external archival mechanisms to make unauthorized alteration detectable.

Such mechanisms demonstrate integrity properties of retained data to the degree their keys, clocks, and storage assumptions remain trustworthy. They do not independently prove that a recorded claim is true, that a source was honest, or that an authorization decision was substantively correct.

---

## Constraints on agents (target)

### Witnessing constraint

An agent may:

- submit an observation, assertion, recommendation, or evidence reference;
- identify a contradiction;
- request verification, retrieval, redaction, retention review, or escalation;
- summarize records while preserving lineage and uncertainty;
- propose a supersession or correction.

An agent may not:

- unilaterally verify its own material claim;
- treat another agent’s output as independent evidence when it shares relevant sources or failure modes;
- suppress conflicting evidence by omitting links or narrowing scope without a logged policy decision;
- make a destructive retention decision merely because a record is inconvenient, stale, or unfavorable;
- approve its own deep-model escalation, budget expansion, governance exception, or external sharing request.

### No governance rewriting

Agents may propose revisions to this contract, access policies, verification matrices, retention rules, domain taxonomies, or escalation thresholds. A proposed revision remains non-operative until a human-approved governance process records:

1. the proposed text and rationale;
2. affected policy and record classes;
3. compatibility and migration implications;
4. review evidence and dissenting views where applicable;
5. human approval identity and effective date;
6. rollback or suspension conditions for reversible changes.

No record status may be upgraded, and no access or retention constraint may be relaxed, merely because an agent interprets a policy differently from the currently recorded policy version.

---

## Document structure for this contract (recommended outline)

| Section | Binding content |
|---|---|
| Purpose and scope | Defines the lifecycle boundary and TARGET status. |
| Hard invariants | Establishes non-negotiable authority, evidence, and escalation constraints. |
| Definitions | Fixes the meaning of records, claims, evidence, independence, and operational use. |
| Record schema | Defines required record fields and their semantics. |
| Epistemic status semantics | Defines the meaning and permitted operational posture of each status. |
| Status transition rules | Defines allowed transitions, preconditions, separation duties, and outcomes. |
| Contradiction handling | Defines dispute representation, resolution outcomes, and preservation duties. |
| Admission rules | Defines lifecycle phases, initial classification, and evidence thresholds. |
| Access and sharing contract | Defines retrieval, material use, least privilege, and sharing audit requirements. |
| Human escalation requirements | Defines triggers, approval boundaries, and escalation-event contents. |
| Auditability and integrity | Defines append-only reconstruction and integrity expectations. |
| Agent constraints | Defines witness limits and governance-change boundaries. |
| Change log | Records approved revisions to this document. |

---

## Open questions (tracked)

| ID | Question | Current contract position | Required resolution path |
|---|---|---|---|
| MLC-001 | How should each `claim_type` map to domain-specific verification evidence? | The risk-class matrix supplies a baseline; detailed domain matrices remain policy work. | Human-approved verification-policy annex. |
| MLC-002 | Should `unknown` be a status or an operational-view override? | This draft treats it as a status so uncertainty is auditable in the journal. | Compatibility review before any schema revision. |
| MLC-003 | How should attribution survive identity migration or model replacement? | `producer_agent_id` preserves historical attribution; successor identity does not inherit verification authority automatically. | Coordinate with the future migration-path contract and human governance review. |
| MLC-004 | How granular must independence modeling become? | `source_independence_group` is the minimum field; it is insufficient where policy needs source, operator, model, toolchain, and assumption layers. | Human-approved evidence-model extension. |
| MLC-005 | How should privacy erasure obligations coexist with evidence preservation? | Preserve a restricted tombstone and audit rationale where content deletion is lawfully required; legal hold and applicable law control conflicts. | Human legal and governance policy decision. |
| MLC-006 | What retrieval-audit detail is proportionate for low-risk exploratory reads? | Material retrieval is always audited; non-material read logging may be narrowed by policy without weakening access enforcement. | Privacy and observability policy review. |

---

## Change log

- `2026-08-16`: Initial TARGET proposal established the record schema, lifecycle states, transition matrix, retention controls, retrieval rules, dispute handling, and human escalation boundaries.
