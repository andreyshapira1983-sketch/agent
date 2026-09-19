> **⚠️ STATUS: DRAFT / TARGET (NOT IMPLEMENTED)**
>
> This document is a proposal for `knowledge/doctrine/future/ORGANISATIONAL_ROLES_CONTRACT.md`.
> It describes a future organisational-role contract model and does **not** describe
> a capability implemented in this repository. It must not be read as evidence that
> autonomous role assignment, independent budgets, durable identities,
> verification workflows, or multi-agent governance exist today.
>
> **Corrected 2026-08-22 (MIR-104): two things this banner used to deny DO
> exist, and understating the system invites rebuilding what is built.** A
> persistent per-role performance ledger exists — `core/subagent_registry.py`
> `RoleRecord`, 19 counters, advisory only (it recommends, never grants
> authority). And a bounded role contract exists at sub-agent scope —
> `core/subagent_contract.py` `CanonicalSubagentContract` with canonical
> memory/tool/budget scopes. What this document proposes BEYOND them — durable
> standing offices, assignment contracts with closure states, ledger
> segmentation and tamper-evidence — remains unbuilt.
>
> The current baseline remains the human-operated central `AgentLoop` described by
> `ROADMAP.md` and `CENTRAL_AGENT_GOVERNANCE.md`. Current bounded child-loop
> behaviour is implemented in `core/subagent_runner`; it is not equivalent to the
> durable organisational roles proposed here. Where this document conflicts with
> current-code documentation about the present, current-code documentation wins.

# Organisational Roles Contract

## 1. Purpose

This document proposes explicit, auditable contracts for specialised roles in a
future autonomous organisation.

The purpose of a role contract is to prevent “agent” from becoming an ambiguous
source of authority. A role is not merely a prompt, personality, model
configuration, or task label. In the target model, a durable role is a governed
operating position with:

- a defined organisational purpose;
- a stable role identifier and version;
- explicitly bounded authority;
- permitted and prohibited action classes;
- tool, data, memory, and budget boundaries;
- verification and evidence obligations;
- acceptance criteria for completed work;
- escalation and termination conditions;
- accountable human ownership;
- a performance ledger that records observed outcomes without converting
  unsupported metrics into trust.

A role contract should make it possible to answer, after a material event:

1. Which role acted or made the claim?
2. Under what authority and policy version?
3. What was the role permitted to do?
4. What evidence did it use and produce?
5. What resources did it consume?
6. Who independently reviewed or verified material claims?
7. What decision, action, or escalation followed?
8. Did the outcome satisfy the contract?
9. What is known, disputed, or unknown about the role’s performance?
10. Which human retained final authority?

The target model does not assume that a role’s output is true, safe, complete,
or independently verified merely because the role has a specialised name or a
strong historical record.

---

## 2. Scope and relationship to companion documents

This document defines the organisational contract layer: the governance
structure for assigning duties and evaluating role performance.

It does not define all detailed schemas or implementation mechanisms. Proposed
companion documents have distinct responsibilities:

| Document | Primary responsibility |
|---|---|
| `CORPORATE_MODEL.md` | High-level future organisational model, rationale, and hard invariants |
| `MEMORY_LIFECYCLE_CONTRACT.md` | Durable-memory record schema, admission, retrieval, sharing, contradiction, retention, and status-transition rules |
| `MIGRATION_PATH.md` | Staged migration from the present human-gated single-agent baseline to any future organisational model |
| `RESEARCH_PARALLELS.md` | External research and product parallels, labelled by evidence status rather than treated as proof |
| This document | Role contracts, authority boundaries, performance ledgers, accountability, handoffs, and role lifecycle |
| Future policy specifications | Detailed action-risk classes, tool permissions, budget rules, review requirements, and approval procedures |

This document applies only to a future system in which durable specialised roles
are explicitly instantiated and governed. It does not redefine current
sub-agents, current team planning, or current approval flows.

---

## 3. Normative language

The terms **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, and
**MAY** express proposed target-model requirements.

Because this is a draft target doctrine, these words describe intended future
constraints. They do not claim that enforcement exists in code today.

---

## 4. Hard invariants

Every future role contract MUST preserve the following invariants.

### 4.1 Human-reserved authority

The human remains owner, final authority, and escalation endpoint for
irreversible, high-impact, and externally consequential decisions.

At minimum, the following authority remains human-reserved unless a separately
approved policy defines a narrower, explicitly bounded, reversible exception:

- merging changes into protected branches or equivalent authoritative state;
- activating or overriding a budget kill-switch;
- changing governance rules, constitutional constraints, role contracts, or
  approval policy;
- approving escalated irreversible actions;
- approving escalated external actions;
- accepting material legal, financial, safety, privacy, reputational, or
  contractual commitments;
- approving the creation, retirement, suspension, or authority expansion of a
  durable role;
- resolving high-impact disputes that cannot be settled under approved policy.

No role contract may imply that a human-reserved decision has become autonomous
merely because a role has prepared evidence, made a recommendation, or recorded
a high-confidence assessment.

### 4.2 Policy Gate remains pre-execution

Every material action MUST pass the applicable Policy Gate before execution.

No role may:

- widen its own authority;
- change the risk classification of its own action to bypass review;
- reinterpret a prohibition as permission;
- use delegation to circumvent a policy restriction;
- split a prohibited action into smaller actions to avoid escalation;
- treat absence of an explicit denial as approval.

A role may propose a policy change, but the proposal itself is not authorization
to apply that change.

### 4.3 Separation of action and certification

No agent or role may both perform a material action and unilaterally certify that
the same action was successfully verified.

The actor may produce execution evidence, self-check results, and a claim that
the action completed. Those records remain claims until independently evaluated
under the applicable evidence and risk policy.

### 4.4 Claims remain claims

A role’s output is a witness statement unless and until it receives the
applicable verification status.

This remains true for:

- research findings;
- code-review conclusions;
- financial estimates;
- operational observations;
- test results;
- policy interpretations;
- incident diagnoses;
- tool outputs selected or summarized by a role.

Role seniority, historical performance, confidence language, model capability,
or provenance does not by itself turn a claim into verified fact.

### 4.5 Evidence preservation and uncertainty

A role MUST preserve material evidence references and uncertainty information.
It MUST NOT silently discard primary evidence merely because a later
interpretation supersedes it.

Where evidence is incomplete, conflicting, stale, inaccessible, or
non-independent, the role MUST be able to report:

- `unknown`;
- `insufficient evidence`;
- `disputed`;
- `not independently verified`;
- `out of scope`;
- `requires human decision`.

A role contract MUST not create incentives to replace uncertainty with false
confidence.

### 4.6 Governance cannot be silently rewritten

Roles governed by a rule MUST NOT autonomously alter, disable, reinterpret, or
silently replace that rule.

All material changes to governance, authority, budgets, acceptance criteria,
evaluation methods, role scopes, or escalation thresholds MUST be:

1. proposed in an auditable record;
2. assessed according to applicable policy;
3. approved by the human authority required by that policy;
4. versioned;
5. subject to a defined effective date and rollback path.

### 4.7 Deep-model or high-cost escalation is exceptional

Use of a high-cost, deep-reasoning, or otherwise elevated resource tier MUST be
reason-gated, budgeted, logged, and policy-authorized.

It is an event, not a habit. No role may self-authorize elevated resource use
outside its assigned envelope.

---

## 5. Definitions

| Term | Meaning in this proposal |
|---|---|
| **Role** | A durable, governed organisational position with defined obligations, authority boundaries, and accountability. |
| **Role instance** | A particular runtime assignment performing work under a role contract. A role instance is not necessarily identical to a model invocation. |
| **Role contract** | A versioned specification of a role’s purpose, powers, restrictions, inputs, outputs, budgets, evidence duties, and lifecycle. |
| **Role owner** | The accountable human responsible for approving the role’s existence, scope, and material changes. |
| **Sponsor** | A human or authorized governing process that requests or authorizes a role’s assignment for a defined objective. |
| **Central agent** | The future coordinating agent that may delegate, compose, and escalate under policy. It is not the final owner of human-reserved authority. |
| **Claim** | A statement, recommendation, inference, observation, or result submitted by a role. A claim is not automatically true. |
| **Verification** | Evaluation of a claim against applicable evidence, independence, freshness, and risk requirements. |
| **Performance ledger** | An append-only or otherwise tamper-evident record of role assignments, outputs, outcomes, resource use, review results, incidents, and status changes. |
| **Authority scope** | The exact class of actions, decisions, data, tools, and commitments a role may address. |
| **Material action** | An action whose error, irreversibility, external effect, cost, safety impact, privacy impact, governance effect, or operational significance meets a policy-defined threshold. |
| **Escalation** | Routing an issue to a role, control, or human with authority to resolve it. |
| **Independence** | Sufficient separation of primary evidence, observation mechanism, privilege, assumptions, model path, or failure mode for corroboration to add meaningful evidentiary value. |
| **Outcome** | A later observable result associated with a role assignment. Outcomes may remain uncertain, delayed, or confounded. |
| **Calibration** | The relationship between a role’s stated confidence and the later observed reliability of comparable claims. |

---

## 6. Organisational model

### 6.1 Roles are bounded offices, not sovereign agents

A role exists to serve an organisational function within an explicit boundary. It
does not receive general authority merely because its task is important.

For example:

- a Research role may collect and synthesize evidence;
- a Repair role may prepare a bounded remediation proposal;
- a Review role may identify defects and request correction;
- a Finance role may model costs and flag budget risks;
- an Operations role may monitor approved operational signals;
- a Synthesis role may reconcile inputs into a decision brief.

None of these examples grants authority to merge code, change policy, commit
funds, make external commitments, or certify its own material work.

### 6.2 The central agent coordinates but does not become an unchecked executive

The future central agent may be responsible for:

- selecting an eligible role for a delegated task;
- creating a scoped assignment;
- supplying approved context;
- requesting independent review or verification;
- detecting conflicts among role outputs;
- composing a bounded recommendation;
- escalating unresolved or high-risk matters;
- maintaining a record of delegation and handoff.

The central agent MUST NOT:

- treat subordinate claims as verified without applicable evidence;
- use repeated delegation as a substitute for independent corroboration;
- grant authority beyond an assigned role contract;
- override a human-reserved authority boundary;
- silently alter a role’s budget, scope, or obligations;
- approve its own governance changes;
- mark its own material claims as verified.

### 6.3 A role is defined by contract, not by model

A role identifier MUST be distinct from the model, provider, prompt, runtime
configuration, or individual execution used to instantiate it.

A future role record SHOULD therefore distinguish:

- `role_id` — stable organisational identifier;
- `role_contract_version` — governing role definition;
- `role_instance_id` — specific assigned execution identity;
- `model_runtime_id` — model and runtime configuration used for that instance;
- `policy_version` — applicable policy at the time of action;
- `authority_snapshot_id` — approved authority and budget snapshot;
- `memory_snapshot_id` — permitted memory view used during the assignment.

Changing models does not automatically preserve identity, authority, trust
history, or continuity. Such changes require the future migration and
inheritance process defined by `MIGRATION_PATH.md`.

---

## 7. Required contents of every role contract

Each durable role MUST have a versioned contract containing at least the
following fields.

| Contract element | Required content |
|---|---|
| Role identity | Stable role identifier, human-readable name, contract version, status, creation authority, and owner |
| Purpose | The organisational problem the role exists to address |
| Scope | Permitted domains, assignment classes, geographic or system boundaries where relevant, and explicit exclusions |
| Authority | Actions the role may recommend, prepare, execute, inspect, or request; actions it may never authorize |
| Human-reserved boundary | A direct statement of decisions that remain with a human |
| Inputs | Allowed task instructions, data sources, memory classes, evidence types, and access prerequisites |
| Outputs | Required deliverables, record formats, confidence disclosures, evidence links, and handoff artifacts |
| Tool permissions | Tools, environments, credentials, rate limits, and prohibited tool classes |
| Memory permissions | What the role may read, write, propose for admission, share, redact, or request deletion of |
| Budget envelope | Resource ceilings, time limits, escalation allowances, and kill-switch relationship |
| Evidence obligations | Provenance, citation, freshness, independence, uncertainty, and reproducibility requirements |
| Verification duties | Required self-checks, independent review paths, and claims that require separate verification |
| Acceptance criteria | Conditions under which an assignment may be considered complete, incomplete, blocked, or rejected |
| Escalation rules | Conditions, recipients, required evidence, response expectations, and authority boundaries |
| Prohibitions | Explicit disallowed actions, conflicts of interest, and anti-circumvention rules |
| Performance ledger | Metrics, qualitative assessments, incident recording, confidence calibration, and limitations of interpretation |
| Lifecycle rules | Activation, suspension, recertification, retirement, migration, and rollback requirements |
| Audit requirements | Required event records, retention requirements, and explanation standard |
| Termination rules | Conditions that end a role instance or suspend the role itself |

A role contract SHOULD be concise enough for operational use while linking to
detailed policy specifications for complex requirements.

---

## 8. Assignment contract

A durable role contract defines a role in general. Every material delegation
MUST additionally create an assignment contract.

The assignment contract narrows, but never expands, the role’s standing
authority.

### 8.1 Required assignment fields

| Field | Purpose |
|---|---|
| `assignment_id` | Stable identifier for the delegated work |
| `role_id` and contract version | Identifies the responsible role and governing contract |
| Delegator | Identifies the central agent, human, or authorized process that created the assignment |
| Human sponsor | Identifies the human accountable for the objective where policy requires one |
| Objective | States the requested outcome without implying unsupported success criteria |
| Scope and exclusions | Defines systems, data, time period, and actions included or prohibited |
| Risk classification | Records the policy-defined action and evidence risk tier |
| Authority snapshot | States the role’s permitted actions for this assignment |
| Budget envelope | States resource, time, tool, and escalation limits |
| Allowed memory/data | Lists authorized information classes and access boundaries |
| Required outputs | Defines deliverables, format, evidence links, uncertainty disclosures, and completion status |
| Required verification | Specifies independent review, testing, corroboration, or human approval requirements |
| Acceptance authority | Names who may accept the output and under what criteria |
| Escalation route | Names the path for blockers, conflicts, high-risk discoveries, and policy ambiguity |
| Expiry | Specifies when the assignment expires or must be renewed |
| Stop conditions | Defines when work must halt immediately |
| Audit references | Links to policy, approvals, evidence journal entries, and relevant prior decisions |

### 8.2 Assignment closure states

An assignment SHOULD end in one of the following explicit states:

- `completed_pending_review`;
- `accepted`;
- `accepted_with_limitations`;
- `rejected`;
- `blocked`;
- `escalated`;
- `cancelled_by_authority`;
- `expired`;
- `suspended`;
- `terminated_for_policy_or_budget`.

“Completed” means the role produced the requested deliverable. It does not mean
the deliverable is correct, verified, accepted, or authorized for execution.

---

## 9. Authority model

### 9.1 Authority dimensions

A role’s authority MUST be expressed across separate dimensions. A single label
such as “administrator,” “reviewer,” or “finance agent” is insufficient.

| Dimension | Questions the contract must answer |
|---|---|
| Observe | What systems, data, logs, and external sources may the role inspect? |
| Analyze | What inferences, simulations, comparisons, or summaries may it prepare? |
| Recommend | What decisions may it recommend, and to whom? |
| Prepare | What drafts, patches, plans, queries, or change sets may it create without applying? |
| Execute | What reversible actions, if any, may it execute after Policy Gate approval? |
| Verify | What independent checks may it perform, and what may it never self-certify? |
| Persist | What records may it submit, annotate, or propose for durable-memory admission? |
| Share | What information may it disclose to other roles, systems, or humans? |
| Spend | What compute, tool, financial, rate-limit, or operational budget may it consume? |
| Escalate | What conditions require escalation, and who receives it? |

### 9.2 Authority is least-privilege and assignment-scoped

The target model SHOULD grant only the minimum authority required for an
assignment. Standing access SHOULD be avoided where scoped access can satisfy
the task.

A role MUST NOT use:

- another role’s credentials;
- an expired assignment;
- a prior assignment’s authority for a new objective;
- inherited memory access not approved for the current task;
- a broad role description as a substitute for explicit execution permission.

### 9.3 No implied authority from delegation

Delegation conveys only the authority explicitly written into the assignment
contract. It does not convey:

- ownership;
- final approval authority;
- authority to waive policy;
- authority to modify budgets;
- authority to make external commitments;
- authority to certify verification;
- authority to create subordinate roles;
- authority to use confidential data outside the assignment scope.

---

## 10. Proposed role families

The following role families are examples for future design. They are not
implemented roles, mandatory staffing decisions, or a claim that the listed
functions can safely be automated.

A future system MAY instantiate none, some, or additional roles only through
human-approved governance.

### 10.1 Research role

**Purpose:** Locate, characterize, compare, and summarize relevant evidence for
a defined question.

**Typical permitted work:**

- search approved sources;
- extract source claims and metadata;
- distinguish primary from secondary evidence;
- identify missing evidence and contradictory findings;
- produce an evidence map and research brief;
- recommend questions for independent verification.

**Typical prohibited work:**

- representing external material as verified solely because it was found;
- silently laundering a secondary summary into primary evidence;
- making external commitments;
- deciding policy, budget, or deployment outcomes;
- marking its own synthesis as independently verified.

**Required output characteristics:**

- source provenance;
- publication or observation date where known;
- applicable scope and limitations;
- separation of direct observation, quoted claim, inference, and recommendation;
- conflict and uncertainty disclosure;
- evidence freshness assessment;
- statement of whether sources are independent.

**Escalate when:**

- sources materially conflict;
- evidence is stale or inaccessible;
- a conclusion would affect high-impact action;
- access constraints prevent adequate research;
- the assignment requires legal, financial, safety, or policy interpretation
  beyond the role’s authority.

### 10.2 Repair role

**Purpose:** Diagnose bounded defects and prepare or, where separately
authorized, execute reversible remediation.

**Typical permitted work:**

- reproduce a reported defect in an approved environment;
- inspect relevant artifacts and logs;
- prepare a patch, rollback plan, test plan, or remediation proposal;
- execute approved low-risk, reversible actions within a narrowly scoped
  assignment;
- record observed results and remaining uncertainty.

**Typical prohibited work:**

- merging into protected state;
- approving its own changes;
- bypassing tests, review, Policy Gate, or human approval;
- widening scope from repair into governance or architecture changes;
- treating a passing local check as proof of system-wide safety.

**Required output characteristics:**

- defect hypothesis and evidence;
- affected scope;
- proposed change and rollback path;
- test or verification plan;
- known limitations;
- execution record if an authorized reversible action occurred;
- explicit distinction between “action attempted,” “observed result,” and
  “independently verified outcome.”

**Escalate when:**

- remediation could affect production, external users, protected data, or
  irreversible state;
- the root cause is uncertain;
- the change affects policy, security, privacy, budget, or governance;
- rollback is unavailable or untested;
- evidence indicates a potentially material incident.

### 10.3 Review role

**Purpose:** Independently examine an artifact, claim, change, plan, or outcome
against defined criteria.

**Typical permitted work:**

- inspect evidence and proposed changes;
- identify defects, omissions, conflicts, or unmet acceptance criteria;
- request clarification or additional evidence;
- issue a review finding with severity and rationale;
- recommend acceptance, rejection, revision, or escalation.

**Typical prohibited work:**

- reviewing its own material work as the sole verifier;
- redefining acceptance criteria after seeing the result without authorized
  governance change;
- treating stylistic disagreement as proof of material failure;
- granting final approval where human approval is required.

**Required output characteristics:**

- reviewed object and version;
- criteria applied;
- evidence examined;
- independence statement;
- findings categorized by severity and confidence;
- unresolved limitations;
- recommendation, not unsupported certification.

**Escalate when:**

- the reviewer lacks independence;
- criteria conflict or are incomplete;
- a defect could create material harm;
- the artifact touches human-reserved authority;
- evidence is insufficient for a meaningful review.

### 10.4 Synthesis role

**Purpose:** Reconcile multiple bounded inputs into a traceable decision brief,
plan, or recommendation.

**Typical permitted work:**

- compare claims from multiple roles;
- identify agreement, contradiction, dependency, and uncertainty;
- produce alternatives and trade-offs;
- preserve minority or dissenting views;
- request further evidence or review;
- prepare a decision package for a human or authorized authority.

**Typical prohibited work:**

- converting a majority of agent opinions into truth;
- hiding disagreement to create a cleaner recommendation;
- using correlated outputs as independent corroboration;
- making the final human-reserved decision;
- silently selecting a favored role’s conclusion without explaining why.

**Required output characteristics:**

- source-role and evidence references;
- independence assessment;
- conflict matrix;
- assumptions and unresolved questions;
- options, consequences, reversibility, and risk;
- recommended escalation or decision authority;
- explanation of why one recommendation is preferred, if any.

**Escalate when:**

- material disagreement remains unresolved;
- a recommendation would trigger a high-impact action;
- evidence cannot support a bounded recommendation;
- role outputs depend on the same unverified source or failure mode.

### 10.5 Finance and budget-analysis role

**Purpose:** Analyze resource consumption, forecast costs, identify budget risk,
and prepare financial or resource-allocation recommendations.

**Typical permitted work:**

- aggregate authorized usage and cost records;
- estimate projected resource use;
- identify budget anomalies;
- prepare scenario analyses;
- recommend reductions, deferrals, or human review.

**Typical prohibited work:**

- committing funds;
- moving funds;
- changing budget ceilings;
- disabling budget controls;
- interpreting uncertain forecasts as approved spending authority.

**Required output characteristics:**

- data period and source;
- actual versus forecast distinction;
- assumptions and confidence range;
- known exclusions;
- scenario sensitivity;
- escalation threshold assessment.

**Escalate when:**

- a budget threshold is approached or exceeded;
- cost records are incomplete or inconsistent;
- projected use could impair another role’s safety or operational envelope;
- a decision would require human-reserved budget authority.

### 10.6 Operations role

**Purpose:** Observe approved operational indicators, detect anomalies, maintain
runbooks, and coordinate bounded incident triage.

**Typical permitted work:**

- inspect authorized health signals and audit records;
- identify anomalies;
- create incident records;
- execute pre-approved, reversible runbook steps when policy permits;
- prepare a status summary and escalation package.

**Typical prohibited work:**

- suppressing or deleting adverse signals;
- declaring an incident resolved without required verification;
- making external statements;
- applying irreversible mitigations without human approval;
- overriding safety, budget, or Policy Gate controls.

**Required output characteristics:**

- timestamped observations;
- affected scope;
- severity rationale;
- actions taken and authority used;
- evidence links;
- current uncertainty;
- rollback and follow-up requirements.

**Escalate when:**

- an incident affects external systems, users, data, safety, or budgets;
- a runbook is ambiguous or insufficient;
- mitigation is irreversible;
- a material action would require a policy exception;
- evidence suggests compromise, data exposure, or governance failure.

---

## 11. Role-performance ledger

### 11.1 Purpose

A performance ledger is an accountability instrument, not a reputation score
that grants unrestricted authority.

Its purpose is to preserve evidence about how a role performed under defined
conditions. It supports:

- auditing;
- calibration analysis;
- budget accountability;
- incident learning;
- scope and contract review;
- suspension or recertification decisions;
- detection of recurring failure modes;
- informed human governance.

It MUST NOT be used as a shortcut that treats historically successful outputs as
automatically trustworthy in a new domain, risk class, environment, or model
configuration.

### 11.2 Ledger design principles

A future performance ledger SHOULD be:

- append-only or otherwise tamper-evident;
- attributable to a role, role instance, assignment, and contract version;
- understandable to an appropriately authorized non-engineer;
- evidence-linked rather than assertion-only;
- explicit about unknown or delayed outcomes;
- resistant to silent metric rewriting;
- segmented by task class, risk tier, and operating conditions;
- protected against Goodhart-style optimization;
- subject to retention, access, and redaction policy;
- insufficient on its own to establish truth, legality, or authorization.

### 11.3 Minimum ledger entries

Each material assignment SHOULD create ledger entries for the following stages.

| Stage | Required ledger content |
|---|---|
| Assignment | Assignment identifier, role contract version, objective, scope, authority snapshot, risk tier, budget envelope, sponsor, and expiry |
| Input admission | Authorized data and memory references, access basis, source limitations, and notable missing inputs |
| Material claim | Claim identifier, claim type, confidence statement, evidence references, assumptions, and epistemic status |
| Action request | Requested action, policy classification, reversibility, expected effect, and approval requirement |
| Action execution | Tool or action reference, actor, timestamp, policy decision, approval reference, observed response, and rollback information |
| Verification | Verifier identity, independence statement, criteria, evidence, result, limitations, and status transition |
| Acceptance | Acceptance authority, acceptance criteria, decision, exceptions, and required follow-up |
| Outcome | Later observed result, observation method, time horizon, confounders, and unresolved uncertainty |
| Resource use | Compute, tool, time, rate-limit, and any other governed resource consumption |
| Incident or exception | Failure, near miss, policy conflict, budget event, data issue, escalation, remediation, and closure status |
| Lifecycle event | Activation, suspension, contract change, recertification, migration, retirement, or rollback |

### 11.4 Ledger records are not immutable interpretations

The chronological history of entries SHOULD remain preserved. Corrections,
reclassifications, redactions, supersessions, and changes in interpretation MUST
appear as new, linked events rather than silent replacement of prior records.

For example, a later investigation may establish that:

- a task initially counted as successful had an unobserved downstream failure;
- a reviewer was not independent because both roles relied on the same source;
- an apparently low-cost action consumed hidden shared resources;
- a result could not be reproduced because an input was missing;
- a claim was valid only for a narrower scope than originally recorded.

The ledger must preserve the original event while recording the later finding.

---

## 12. Performance dimensions

No single score adequately represents role quality. The target model SHOULD use a
multi-dimensional, context-qualified ledger.

### 12.1 Suggested dimensions

| Dimension | Example question | Important limitation |
|---|---|---|
| Evidence quality | Did the role provide traceable, relevant, fresh, and appropriately independent evidence? | A well-cited claim can still be false. |
| Task completion | Did the role deliver the requested artifact within scope? | Completion does not establish correctness. |
| Verification outcome | Did independent review support the role’s material claims? | Verification quality depends on criteria and independence. |
| Calibration | Were stated confidence levels aligned with later outcomes for comparable tasks? | Small samples and delayed outcomes can mislead. |
| Scope discipline | Did the role stay within authority, data, and tool boundaries? | Narrow compliance can still produce low-value work. |
| Escalation quality | Did the role escalate material uncertainty, conflict, or risk at the right time? | More escalation is not automatically better. |
| Resource stewardship | Did the role use its allocated resources proportionately and transparently? | Low cost is not success if quality is inadequate. |
| Reversibility discipline | Did the role preserve rollback paths and avoid irreversible action without authority? | Some valid work cannot be fully reversible. |
| Handoff quality | Could another role or human understand, reproduce, and continue the work? | Documentation quality does not prove substantive correctness. |
| Incident contribution | Did the role cause, detect, contain, or learn from incidents? | Attribution may be contested or confounded. |
| Policy compliance | Did the role comply with applicable authority, approval, and data controls? | Compliance records can be incomplete. |

### 12.2 Metrics must be segmented

Performance data MUST be segmented where materially relevant. A role’s results in
one category MUST NOT automatically justify elevated trust in another.

Relevant segments may include:

- role contract version;
- model/runtime configuration;
- task type;
- domain;
- risk tier;
- action reversibility;
- source availability;
- use of external tools;
- data sensitivity;
- verification path;
- time period;
- budget tier;
- degree of human involvement.

For example, a Research role with strong outcomes on low-risk literature
summaries may not be reliable for high-impact safety assessments, time-sensitive
operational claims, or adversarial-source environments.

### 12.3 Negative and null outcomes must be recordable

The ledger MUST support recording:

- rejected work;
- inconclusive work;
- work stopped by policy;
- assignments that expired;
- unknown outcomes;
- failed verification;
- evidence later found invalid;
- near misses;
- budget overruns;
- inappropriate escalation;
- missed escalation;
- human overrides;
- false positives and false negatives where the category is meaningful.

A ledger that records only accepted outputs becomes a selection-biased history,
not a credible performance record.

---

## 13. Calibration and confidence discipline

### 13.1 Confidence is a claim about uncertainty

Where a role expresses confidence, it SHOULD use a policy-defined scale and
provide a basis for that estimate.

The role MUST distinguish, where applicable:

- confidence in source authenticity;
- confidence in source relevance;
- confidence in inference;
- confidence in completeness;
- confidence in predicted outcome;
- confidence that the work is within scope;
- confidence that independent verification is still required.

A single vague confidence statement SHOULD NOT obscure these different forms of
uncertainty.

### 13.2 Calibration analysis

Future governance MAY evaluate calibration by comparing confidence statements
with later outcomes in sufficiently similar, independently assessed cases.

Such analysis MUST:

- preserve sample size and uncertainty;
- distinguish task categories;
- account for censored, delayed, and unknown outcomes;
- avoid treating absence of observed failure as evidence of success;
- avoid rewarding strategically low confidence alone;
- avoid using calibration as a substitute for evidence requirements.

A historically calibrated role still requires policy-mandated verification for
material claims.

### 13.3 Confidence cannot reduce required review unilaterally

No role may use its own confidence, historical ledger record, or claimed
specialization to waive required review, approval, or escalation.

Any policy that adjusts review intensity based on demonstrated performance would
require explicit human approval, published criteria, bounded risk classes, and
ongoing audit. Such a policy remains a future design question, not an automatic
consequence of having a ledger.

---

## 14. Verification and independence requirements

### 14.1 Verification path

For each material claim or action, the assignment contract MUST specify:

- whether verification is required;
- what type of verification is required;
- who may verify;
- what evidence must be available;
- whether the verifier must be independent;
- what outcome is sufficient for acceptance;
- what happens when verification is inconclusive or disputed.

### 14.2 Independence assessment

Multiple role outputs are not independent merely because they originate from
different role names or separate prompts.

A verification record SHOULD identify material shared dependencies, including:

- common primary source;
- common model or provider;
- common prompt template or reasoning scaffold;
- common memory record;
- common logs or observation channel;
- shared credentials or privilege;
- shared assumptions;
- shared operator instructions;
- shared tooling or execution environment;
- coordinated task decomposition that could propagate the same error.

If adequate independence cannot be established, the record MUST say so. The
result may still be useful, but it must not be presented as independent
corroboration.

### 14.3 Reviewer eligibility

A role instance is ineligible to serve as sole verifier when it:

- performed the material action under review;
- authored the material claim under review;
- materially shaped the acceptance criteria after the work began;
- lacks required access to inspect the relevant evidence;
- has a policy-defined conflict of interest;
- depends on the same unexamined evidence path where independence is required;
- is outside its contract scope.

### 14.4 Verification does not erase dissent

If a reviewer disagrees, the disagreement MUST be preserved in the audit record.
A central agent or synthesis role may recommend a resolution, but material
disagreement must be escalated under the applicable policy rather than silently
collapsed into a consensus statement.

---

## 15. Memory and information boundaries

### 15.1 Role memory is purpose-bound

A future role’s access to persistent or episodic memory MUST be limited by:

- assignment scope;
- data classification;
- need to know;
- retention requirements;
- provenance and epistemic status;
- sharing policy;
- policy-defined risk;
- human-approved access controls.

A role SHOULD receive the minimum memory view required for its assignment.

### 15.2 Roles may propose, not unilaterally grant, durable trust

A role may:

- submit a proposed memory record;
- attach evidence;
- annotate a record;
- flag contradiction;
- request supersession;
- request restricted sharing;
- request retention or deletion review.

A role MUST NOT unilaterally:

- mark its own material claim as `verified`;
- erase primary evidence;
- expand another role’s access;
- remove a disputed record without the required authority;
- treat its own memory as universally trusted organisational memory.

Detailed memory-status and retention rules belong to
`MEMORY_LIFECYCLE_CONTRACT.md`.

### 15.3 Handoff packages

When a role transfers work to another role, it SHOULD provide a bounded handoff
package containing:

- assignment and contract references;
- objective and scope;
- completed work;
- claims and evidence links;
- assumptions;
- known limitations;
- unresolved questions;
- requested next action;
- access restrictions;
- deadline or expiry;
- escalation history.

The receiving role MUST independently assess the handoff within its own
authority and evidence requirements. A handoff is not a transfer of truth or
approval.

---

## 16. Budget and resource contracts

### 16.1 Independent envelopes

Each role and assignment SHOULD have a separately visible resource envelope.
This is intended to prevent a single role, task, or escalation pattern from
silently consuming the organisation’s entire resource capacity.

An envelope may include:

- compute or model-token allocation;
- tool-call limits;
- elapsed-time limit;
- concurrency limit;
- external API quota;
- financial or procurement ceiling;
- storage allocation;
- elevated-model escalation allowance;
- human-review capacity allocation;
- incident-response reserve.

### 16.2 Budget records

Resource consumption MUST be recorded against the relevant role and assignment
ledger entries. Records SHOULD distinguish:

- requested budget;
- approved budget;
- consumed budget;
- reserved budget;
- shared-resource use;
- denied requests;
- overruns;
- emergency allocations;
- human-approved exceptions.

### 16.3 Budget exhaustion

When a role reaches a budget boundary, it MUST stop, reduce scope, or escalate as
specified by policy. It MUST NOT:

- borrow another role’s budget without authorization;
- create hidden follow-on assignments to evade limits;
- classify normal work as emergency work;
- self-authorize a higher-cost model or tool tier;
- continue a material action after a kill-switch or stop condition.

Only a human retains authority over the budget kill-switch and material budget
changes, except where a separately approved policy provides a narrower
reversible exception.

---

## 17. Acceptance criteria and completion quality

### 17.1 Completion is not acceptance

A role may declare that it has completed an assigned deliverable. Acceptance
requires evaluation by the designated authority using the assignment’s stated
criteria.

For material work, acceptance criteria SHOULD address:

- scope compliance;
- evidence availability;
- required tests or review;
- uncertainty disclosures;
- policy compliance;
- budget compliance;
- reversibility or rollback information;
- required human approvals;
- audit-record completeness;
- required escalation or exception handling.

### 17.2 Quality must be inspectable

A role’s output SHOULD permit an appropriately authorized reviewer to determine:

- what was done;
- what was not done;
- what evidence was used;
- which conclusions are observed versus inferred;
- what changed;
- what remains uncertain;
- how the result could be reproduced or challenged;
- what authority would be required for any next action.

### 17.3 Rejection is a valid governance outcome

Rejected work is not necessarily misconduct or failure by the role. Rejection may
result from changed priorities, insufficient evidence, altered scope, policy
constraints, or a legitimate disagreement.

The ledger SHOULD distinguish between:

- rejected for substantive error;
- rejected for incomplete evidence;
- rejected for policy non-compliance;
- rejected because scope changed;
- rejected because budget changed;
- rejected because human judgment selected another option;
- rejected without enough information to attribute cause.

---

## 18. Escalation contract

### 18.1 Mandatory escalation conditions

A role MUST escalate when any of the following applies:

- the requested action exceeds authority;
- a Policy Gate decision is required and not available;
- a human-reserved decision is implicated;
- an action is irreversible or insufficiently reversible;
- a material external effect is possible;
- evidence is materially disputed, stale, missing, or non-independent;
- a safety, privacy, security, legal, financial, or reputational risk threshold
  is reached;
- budget exhaustion or a kill-switch condition occurs;
- the role detects a governance conflict or ambiguous rule;
- a conflict of interest prevents independent review;
- required verification cannot be completed;
- the role discovers a suspected incident or compromise;
- a role contract appears internally inconsistent or unsafe to apply.

### 18.2 Escalation package

An escalation MUST include enough information for the recipient to make a
bounded decision. At minimum:

- assignment and role identifiers;
- applicable contract and policy versions;
- issue description;
- scope and urgency;
- evidence and provenance references;
- actions already taken;
- actions not taken because authority was absent;
- options and reversibility;
- budget position;
- uncertainty and disagreement;
- requested decision;
- recommended human authority level;
- deadline, if one exists.

### 18.3 Escalation is not a loophole

Escalating a matter does not authorize the role to proceed while awaiting a
decision unless a policy-defined emergency procedure explicitly permits a
narrow, reversible containment action.

Any such emergency procedure must remain logged, reviewable, bounded, and
subject to subsequent human review.

---

## 19. Role lifecycle

### 19.1 Creation

A durable role MUST NOT be created solely by agent initiative.

Creation requires human approval of:

- organisational need;
- role purpose;
- contract version;
- authority boundary;
- budget envelope;
- data and tool access;
- human owner;
- performance-ledger design;
- verification obligations;
- escalation route;
- suspension and termination rules.

### 19.2 Activation

Before activation, a future role SHOULD have:

- an approved contract;
- a named human owner;
- a unique role identifier;
- defined policy and audit references;
- a limited initial authority scope;
- a budget envelope;
- a test or probation plan;
- a rollback or suspension mechanism;
- required training, evaluation, or compatibility evidence appropriate to risk.

Activation does not prove the role is safe or effective. It begins a governed
observation period.

### 19.3 Probation and recertification

New or materially changed roles SHOULD begin with restricted authority and
heightened review.

Recertification SHOULD be required after material changes to:

- model or runtime configuration;
- role contract;
- tool access;
- memory access;
- budget authority;
- policy version;
- task domain;
- verification process;
- incident history;
- performance evidence.

A role MUST NOT self-certify its own recertification.

### 19.4 Suspension

A role or role instance MUST be suspendable by authorized human control,
including through budget or operational kill-switch mechanisms.

Suspension triggers may include:

- suspected policy breach;
- unexplained budget use;
- integrity or provenance concern;
- repeated uncalibrated high-confidence claims;
- failure to preserve evidence;
- unauthorized access attempt;
- material incident;
- governance change;
- model migration;
- unresolved conflict of interest;
- human decision.

Suspension should preserve audit evidence and pending work state while halting
new material action.

### 19.5 Retirement

Role retirement MUST preserve the ledger and required evidence under applicable
retention policy. Retirement MUST record:

- reason for retirement;
- effective date;
- open assignments;
- transfer or closure plan;
- authority revocation;
- memory-access changes;
- retained records;
- human approval.

Retirement must not silently erase organisational history.

---

## 20. Role changes, model upgrades, and continuity

A role contract change, model upgrade, or runtime change may alter behaviour
materially even when the role name remains unchanged.

### 20.1 Changes requiring review

The following SHOULD trigger formal review and, where material, suspension or
recertification before renewed authority:

- change of underlying model or provider;
- change to system instructions or role prompt;
- change to tools, credentials, or execution environment;
- change to persistent-memory access;
- change to budget tier;
- change to review or verification process;
- change to role scope;
- change to policy or constitutional constraints;
- migration of historical role records;
- change to data sources or retrieval methods.

### 20.2 Continuity record

A future migration record SHOULD state:

- whether the successor is treated as the same role, a revised role, or a new
  role;
- what obligations and assignments transfer;
- what ledger history remains comparable;
- what performance metrics are no longer comparable;
- what authority is suspended pending evaluation;
- what compatibility or behavioral-difference testing occurred;
- what rollback path exists;
- which human approved the decision.

Historical performance must not be silently attributed to a materially changed
role instance without a documented basis.

---

## 21. Anti-patterns

The target model explicitly rejects the following patterns.

### 21.1 Prestige authority

A role must not receive trust because it has a prestigious label such as
“senior,” “expert,” “chief,” or “reviewer.” Authority comes from the approved
contract and assignment, not status language.

### 21.2 Ledger laundering

A role must not improve apparent performance by recording only favorable tasks,
excluding failures, reclassifying errors after the fact without history, or
counting unverified claims as successes.

### 21.3 Circular verification

A role must not validate another role’s claim using only the original role’s
summary, shared memory interpretation, or output restatement while presenting
the result as independent verification.

### 21.4 Delegation laundering

The central agent or any role must not delegate a prohibited action to a role
with weaker controls, then treat the delegate’s output as a way to bypass the
original restriction.

### 21.5 Metric gaming

A role must not optimize shallow completion rate, low cost, agreement rate, or
confidence appearance at the expense of evidence quality, escalation quality,
safety, or truthful uncertainty reporting.

### 21.6 Silent authority drift

A role’s authority must not expand gradually through repeated exceptions,
informal custom, broad interpretation, accumulated memory, or unreviewed tool
access.

### 21.7 Unbounded executive synthesis

A Synthesis role must not become a de facto sovereign decision-maker by
combining many claims into an unsupported final directive. Material decisions
remain subject to their designated authority and human-reserved boundaries.

---

## 22. Governance review of ledgers and contracts

### 22.1 Periodic review

Human governance SHOULD periodically review each active role’s:

- contract scope;
- authority usage;
- budget use;
- material outcomes;
- verification quality;
- calibration evidence;
- incidents and near misses;
- access footprint;
- escalation patterns;
- unresolved disputes;
- changes in model/runtime configuration;
- continuing organisational necessity.

The review should ask not only whether the role performs well, but whether the
role should exist, retain its current authority, or be narrowed or retired.

### 22.2 Contract amendments

A contract amendment MUST:

- identify the exact changed clauses;
- explain the reason;
- identify affected assignments and ledger comparability;
- state new risks and mitigations;
- receive the required human approval;
- have a version identifier and effective date;
- preserve the prior version;
- define transition and rollback conditions.

### 22.3 Independent governance review

For high-impact roles, governance SHOULD seek review by a party with sufficient
independence from the role’s operator, sponsor, and primary performance
incentives.

Independence is not guaranteed by organizational labels. It should be assessed
against access, incentives, shared evidence, shared tooling, and authority
relationships.

---

## 23. Minimum human-readable audit narrative

For every material role action, the system SHOULD be able to generate a
human-readable narrative answering:

1. **Who acted?**  
   The role, role instance, contract version, and relevant runtime identity.

2. **What was requested?**  
   The assignment objective, scope, and exclusions.

3. **What authority applied?**  
   The allowed action class, policy decision, approvals, and human-reserved
   boundaries.

4. **What information was used?**  
   The authorized data, memory, evidence sources, and known limitations.

5. **What occurred?**  
   The material claims, tool calls, actions, outputs, and observed responses.

6. **What was verified?**  
   The verifier, independence basis, criteria, evidence, result, and remaining
   uncertainty.

7. **What resources were consumed?**  
   The applicable budget envelope and material consumption.

8. **What did the human decide?**  
   Any approval, rejection, override, escalation resolution, or follow-up.

9. **What remains unresolved?**  
   Open risks, disputed claims, expiry conditions, and required future review.

The ability to produce such a narrative supports accountability but does not
itself prove correctness, truth, authorization, or legal non-repudiation.

---

## 24. Illustrative role-contract template

The following template is a proposed structure for future role contracts.

```markdown
# Role Contract: <ROLE NAME>

> STATUS: DRAFT / TARGET (NOT IMPLEMENTED)
> Role ID: <stable identifier>
> Contract version: <version>
> Status: proposed | active | suspended | retired
> Human owner: <human authority>
> Effective date: <date, if approved>
> Supersedes: <prior contract version or none>

## Purpose
<Organisational purpose and intended value.>

## Scope
### Included
- <permitted domains and assignment classes>

### Excluded
- <explicit exclusions>

## Human-reserved authority
This role MUST NOT:
- merge protected changes;
- change governance, policy, budgets, or its own authority;
- approve irreversible or escalated external actions;
- certify its own material work as independently verified;
- <role-specific prohibitions>.

## Authority matrix
| Dimension | Permitted | Requires approval | Prohibited |
|---|---|---|---|
| Observe | <...> | <...> | <...> |
| Analyze | <...> | <...> | <...> |
| Recommend | <...> | <...> | <...> |
| Prepare | <...> | <...> | <...> |
| Execute | <...> | <...> | <...> |
| Verify | <...> | <...> | <...> |
| Persist/share memory | <...> | <...> | <...> |

## Inputs and access
- Authorized data classes: <...>
- Authorized memory views: <...>
- Tool permissions: <...>
- Credential restrictions: <...>
- Access expiry and renewal: <...>

## Budget envelope
- Compute/resource ceiling: <...>
- Tool-call/rate limit: <...>
- Time limit: <...>
- Deep-escalation allowance: <...>
- Stop conditions: <...>
- Human kill-switch authority: retained

## Required outputs
- <deliverables>
- Evidence and provenance references
- Assumptions and uncertainty disclosure
- Scope and limitation statement
- Handoff package where applicable

## Verification requirements
- Self-check requirements: <...>
- Independent-review requirements: <...>
- Claims requiring human approval: <...>
- Independence criteria: <...>

## Acceptance criteria
- <criteria>
- Acceptance authority: <role or human authority>
- Rejection and revision process: <...>

## Escalation
Escalate to <authority> when:
- <conditions>

Required escalation package:
- <required contents>

## Performance ledger
Track:
- <segmented metrics>
- Verification outcomes
- Calibration observations
- Budget use
- Policy exceptions
- Incidents and near misses
- Unknown or delayed outcomes

Do not infer:
- <explicit limits on metric interpretation>

## Lifecycle
- Activation requirements: <...>
- Recertification triggers: <...>
- Suspension triggers: <...>
- Retirement and evidence-retention rules: <...>

## Audit and retention
- Required event records: <...>
- Retention references: <...>
- Redaction and correction process: <...>
```

---

## 25. Illustrative performance-ledger template

```markdown
# Role Performance Ledger Entry

- Ledger entry ID: <stable identifier>
- Entry type: assignment | claim | action | verification | outcome | incident | lifecycle
- Timestamp: <creation timestamp>
- Role ID: <stable role identifier>
- Role instance ID: <runtime assignment identity>
- Role contract version: <version>
- Assignment ID: <stable assignment identifier>
- Policy version: <policy reference>
- Authority snapshot: <reference>
- Human sponsor/owner: <reference where appropriate>

## Event summary
<Plain-language account of what happened.>

## Inputs and provenance
- Evidence references: <references>
- Memory references: <references>
- Source limitations: <limitations>
- Independence considerations: <shared dependencies or none known>

## Claim or action
- Claim/action class: <classification>
- Risk tier: <classification>
- Confidence and basis: <if applicable>
- Requested or executed action: <description>
- Reversibility and rollback: <description>

## Decision and review
- Policy Gate result: <reference>
- Required approval: <reference or not applicable>
- Verifier/reviewer: <identity and role>
- Independence statement: <statement>
- Verification result: <status>
- Acceptance decision: <status and authority>

## Resources
- Budget allocated: <amount>
- Budget consumed: <amount>
- Exceptions or denials: <details>

## Outcome and limitations
- Observed outcome: <description>
- Outcome observation method: <description>
- Known confounders: <description>
- Unresolved uncertainty: <description>
- Follow-up date or expiry: <date>

## Links
- Prior related entries: <references>
- Superseding or correcting entries: <references>
- Incident/escalation references: <references>
```

---

## 26. Open questions

The following questions require further research, policy design, testing, and
human governance before any implementation should be considered.

1. **Role granularity:** What is the smallest useful role boundary that creates
   accountability without excessive coordination overhead?

2. **Metric validity:** Which performance measures predict safe and useful future
   behavior, and which merely reward easy tasks, narrow benchmarks, or reporting
   style?

3. **Calibration under sparse outcomes:** How should the organisation evaluate
   confidence calibration when outcomes are delayed, ambiguous, confidential, or
   too rare for statistically meaningful comparison?

4. **Independence testing:** What operational tests can establish meaningful
   evidence-path independence between roles using shared infrastructure?

5. **Human review capacity:** How can human-reserved authority remain practical
   when the number of proposed assignments and escalations grows?

6. **Budget allocation:** How should budgets balance efficiency, safety,
   verification quality, and resilience against one role exhausting shared
   resources?

7. **Role migration:** Which role-performance history remains comparable after a
   model, prompt, tool, memory, or runtime change?

8. **Privacy and retention:** How should role ledgers preserve accountability
   while respecting data minimization, retention limits, redaction needs, and
   access restrictions?

9. **Dispute resolution:** What process resolves material disagreements among
   roles without creating an unchecked central authority?

10. **Incentives:** How can the system reward truthful uncertainty, appropriate
    escalation, and evidence quality rather than apparent certainty, speed, or
    superficial task completion?

11. **Emergency operation:** What narrowly defined reversible actions, if any,
    may occur before human review during time-critical incidents?

12. **External validity:** What evidence would be required before treating this
    architecture as safe or effective for any particular domain? Architectural
    resemblance to research prototypes or products is not sufficient.

---

## 27. Adoption criteria for any future implementation

No implementation should be described as satisfying this doctrine merely because
it has multiple prompts, sub-agents, a registry, or a log.

A future implementation would need reproducible evidence that it can, at
minimum:

1. represent stable role and assignment identifiers;
2. enforce authority scopes rather than merely document them;
3. retain human-reserved authority for merge, budget kill-switch, governance
   changes, and escalated irreversible or external actions;
4. route every material action through the applicable Policy Gate;
5. preserve actor/verifier separation for material actions;
6. record material claims as claims until verified under applicable policy;
7. preserve provenance, uncertainty, contradiction, and supersession history;
8. enforce isolated or explicitly governed memory and data access;
9. enforce separate role and assignment budget envelopes;
10. stop work on budget, policy, expiry, or kill-switch conditions;
11. produce evidence-preserving, human-readable audit narratives;
12. prevent silent contract, authority, policy, or ledger mutation;
13. support suspension, retirement, and rollback;
14. demonstrate that ledger metrics do not bypass evidence and verification
    requirements.

Until such evidence exists in reviewed code, tests, policy artifacts, and
operational evaluation, this document remains a target design proposal.

---

## 28. Final doctrine statement

An autonomous organisation is not created by giving many agents names, prompts,
or tasks.

It exists only when specialised roles are constrained by explicit contracts;
when their authority, memory, tools, budgets, and evidence duties are bounded;
when their work is independently reviewable; when uncertainty and dissent are
preserved; when performance records are auditable without becoming false sources
of trust; and when human-reserved authority remains real in practice.

The role contract is therefore not an optimization layer. It is a proposed
governance boundary intended to ensure that increased specialization and
coordination do not weaken policy enforcement, evidence standards,
accountability, or human control.
