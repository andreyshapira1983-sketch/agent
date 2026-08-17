# STATUS: FUTURE / TARGET (not implemented)
# knowledge/doctrine/future/AGENT_ROLE_CONTRACT.md

## Purpose

This document proposes a **template structure and binding rules** for creating durable role contracts in the long-horizon “autonomous organisation” model.

It does **not** claim any of the target capabilities are implemented. Any module names referenced below are used only as *named anchors* for where similar concepts may live (or already exist in read-only form) and must not be treated as proof of behavior.

This contract specification is intended to be used to define the durable roles described as future organisational roles in `CORPORATE_MODEL.md`.

---

## Hard invariants (must be preserved by every role contract)

Every durable role contract defined using this template MUST comply with the following invariants:

1. **Policy Gate pre-exec checkpoint**
   - Every material action requires passing the **Policy Gate** before execution.
   - No role may widen policy for itself.

2. **Human-reserved authority remains**
   - **Merge**, **budget kill-switch**, **governance changes**, and approval of **escalated irreversible or external actions** remain human-controlled unless a narrowly scoped policy explicitly defines a reversible exception.
   - A role contract may request actions or evidence, but it must not unilaterally approve them when they fall under human-reserved authority.

3. **Sub-agent claims are witnesses, not verified sources**
   - Any claim produced by a role is a **claim** and is treated as **witness** output.
   - The system’s verification policy determines whether it becomes trusted memory / believed operational state.
   - A role must not both:
     - perform a material action, and
     - certify the same action as successfully verified.

4. **No silent governance mutation**
   - Governance rules cannot be autonomously rewritten by agents they govern.

5. **Evidence preservation**
   - Primary evidence must not be silently destroyed merely because interpretation changes or a later record supersedes it.

6. **Disputed/high-impact escalation is mandatory**
   - Disputed or high-impact records must have an explicit escalation path.

7. **Opus/deep escalation is an event**
   - It must be reason-gated, logged, budgeted, and never self-authorised.

---

## Authority model

### 1) Authority scope vs. action permission
Each role contract MUST separate:

- **Authority scope**: what the role is allowed to decide, draft, recommend, or propose.
- **Action permission**: what the role’s outputs may trigger in the system, subject to the **Policy Gate** and human-reserved authority.

A role may have broad **drafting** authority but narrow **material action** permissions.

### 2) Role authority tiers (required)
Define one of the following tiers for each role and for each action category the role touches:

- **Draft-only**: outputs are proposals, never material.
- **Policy-gated**: role may request actions, but execution depends on the Policy Gate.
- **Witness-verification candidate**: role may produce evidence/analysis but cannot certify its own material actions as verified.
- **Trusted-claim candidate**: role may propose a status transition in memory; the transition still follows verification policy and may require escalation.
- **Escalation requester**: role can prepare and justify escalation requests, but cannot execute human-reserved approval endpoints.

### 3) Human-reserved endpoints (required explicit list)
Each role contract MUST include a section listing categories that it **must not** approve or self-execute, even if technically capable, unless policy explicitly defines a reversible exception.

At minimum, list:
- merge operations
- budget kill-switch changes
- governance rule changes
- approval of escalated irreversible/external actions

---

## Tools and interfaces

### 1) Tool inventory (required)
For each role contract, provide:

- **Tool name** (string)
- **Owning module name** (if known; otherwise write `UNSPECIFIED`)
- **Purpose** (one sentence)
- **Allowed inputs** (what the role may pass)
- **Outputs** (what the role may return/record)
- **Materiality class**: `Non-material`, `Material-proposal`, `Material-exec`

#### Module naming rule (do not overclaim)
Only claim tool existence if you can name the module. If you cannot name the module for a tool, it must be recorded as `UNSPECIFIED` and treated as a design intention, not current capability.

Examples of named module anchors that may be referenced (if applicable to the repository context):
- `core/subagent_registry` (read-only seed concept)
- `core/subagent_runner` (current sub-agent behavior in baseline)
- `core/team_*` and `core/policy` and `core/approval*` (policy and human-approval mechanisms)

But the presence of these names does not imply that any particular tool is available to durable roles in target state.

### 2) Tool usage constraints (required)
Each role MUST specify:
- whether it may call tools in parallel
- maximum number of tool calls per task
- required evidence attachments for tool calls (if any)
- prohibition on tool calls that would bypass Policy Gate or human-reserved authority

---

## Budgets and kill-switches

### 1) Budget model (required fields)
For each durable role contract, specify:

- **Monthly/period budget** (units: tokens, CPU-time, tool-calls, or composite; must be explicitly defined)
- **Per-task budget cap**
- **Retry limit** (planning attempt/replanning attempt)
- **Escalation budget reservation** (if escalation uses separate budget)
- **Kill-switch behavior**: what happens when budget is exceeded

### 2) Budget kill-switch human-reservation
If a kill-switch can affect global system resources, it must be under human-reserved authority. A role contract may define *how the role reacts* (e.g., stop, report), but must not enable kill-switch activation.

### 3) Budget accounting rules (required)
A role contract MUST state:

- what counts as “spend” (tool calls? evidence retrieval? memory writes?)
- whether “draft-only” activities consume budget
- how budget consumption is logged for auditability

---

## Memory scope and epistemic lifecycle integration

This section is the most binding part of a role contract because it governs durable state.

### 1) Memory scope (required)
For each role, define:

- **Memory types** the role may write to (e.g., `episodic`, `persistent`, `audit-ledger`, or refer to `MEMORY_LIFECYCLE_CONTRACT.md` categories)
- **Domains** it may write about
- **Time bounds** (freshness/expiry policies)
- **Cross-role sharing policy** (what may be shared with other roles and under what risk limitations)

### 2) Evidence/provenance requirements (required)
If the role writes durable claims, the role contract MUST specify:

- required provenance fields for its records
- required evidence types (primary sources, logs, tool outputs, observed traces)
- whether it can create records marked `asserted` vs `verified` (and under what conditions)

### 3) Verification and status transitions (required)
A role contract MUST explicitly define its allowable role in the verification pipeline:

- Can it request transitions (e.g., `unknown` → `asserted`)?
- Can it request transitions to `verified`?
- Does it have permission to certify its own material actions?

This MUST comply with hard invariants: **it may not certify its own action as verified**.

### 4) Contradiction handling & escalation (required)
If a role encounters:
- a direct contradiction with existing high-impact memory, or
- a high-impact claim with insufficient evidence,
then it must follow the escalation path defined in this contract.

### 5) Memory boundaries (required)
The contract MUST forbid:
- writing into memory namespaces not owned by the role (unless via shared policy lanes)
- silently overwriting old records
- deleting primary evidence merely because interpretation changes

Deletion/retention changes must be explicit, logged events governed by human-reserved authority or policy-defined reversibility.

---

## Acceptance criteria (task-level)

Each role contract MUST define acceptance criteria for outcomes it produces.

### 1) Outcome types (required)
At minimum, specify which of the following the role may produce:

- plan drafts
- analyses with evidence
- reconciliation proposals
- budget reports
- escalation packages
- operational recommendations
- memory admission proposals

### 2) Acceptance checklist template (required)
For each outcome type, include:

- **Completeness**: required sections present
- **Evidence adequacy**: required evidence referenced
- **Uncertainty preservation**: does not convert unknown/disputed into asserted confidence
- **Policy alignment**: respects Policy Gate and human-reserved endpoints
- **Auditability**: includes pointers to inputs/tool responses used
- **Non-self-certification**: does not claim verified status for its own material actions
- **Contradiction handling**: explicitly notes conflicts and intended resolution path

---

## Escalation conditions (event, not habit)

### 1) Escalation triggers (required)
Define specific conditions that require escalation, such as:

- disputed memory record
- high-impact action requiring human approval
- insufficient evidence to justify memory transition
- budget anomalies (runaway tool calls, repeated failures)
- governance rule conflicts
- suspected policy violation attempts (accidental or adversarial)

### 2) Escalation package schema (required)
An escalation request created by the role must include:

- what is being escalated
- why escalation is needed (trigger)
- what evidence was used
- what would be allowed under Policy Gate if escalation succeeds
- what budgets are requested and remaining
- risk analysis summary (risk class and reversibility)
- proposed human decision options (at least one clear “approve/deny/modify” style option)

### 3) Escalation budgets and logs (required)
- Escalation consumes defined budget.
- Escalation actions are logged for auditability.
- Human approval endpoints remain human-reserved.

---

## Termination rules (role lifecycle)

### 1) Normal termination (required)
Define role completion conditions, such as:

- deliverable accepted under acceptance criteria
- task horizon reached
- user/system directive ends the role’s assignment

### 2) Exceptional termination (required)
Define stop conditions including:

- policy violation detected
- repeated evidence failure / inability to meet acceptance criteria within retry limit
- budget exhaustion
- irreconcilable contradictions requiring human resolution
- human cancels the role assignment

### 3) Cleanup requirements (required)
On termination, role contract MUST specify:

- what memory is written (if anything) and with what status
- what memory is not written
- whether an audit/ledger event is mandatory
- whether the role must produce a final “state handoff” package to another role or the central agent

---

## Durable role contract template (copy/paste)

When creating `AGENT_ROLE_CONTRACT.md`-style entries for a specific durable role, follow this structure exactly:

```md
# Durable Role Contract: <ROLE_NAME>

## 1. Role identity
- Role name:
- Stable identity basis (design intent; UNSPECIFIED if unknown):
- Owned memory domains:
- Primary accountability owner (central agent vs committee vs human):

## 2. Authority and action permissions
- Authority tier (from the required tiers list):
- Allowed outputs (draft-only / recommendations / escalation packages / evidence):
- Disallowed actions (must include human-reserved endpoints):
- Policy Gate dependency statement:

## 3. Tools and interfaces
- Tool inventory (table):
  | Tool name | Module name | Purpose | Materiality class | Constraints |
- Tool call constraints:
- Evidence attachment requirements:

## 4. Budget and kill-switch
- Period budget:
- Per-task cap:
- Retry limit:
- Escalation reservation:
- Kill-switch behavior (role reaction only; no human-reserved activation):

## 5. Memory scope & epistemic lifecycle
- Memory types it may write:
- Admission rules (what must be present in records):
- Allowed status transitions (e.g., unknown→asserted; asserted→verified only if allowed by policy):
- Sharing policy:
- Contradiction and supersession handling:

## 6. Acceptance criteria
- Outcome types the role produces:
- Acceptance checklist per outcome type:

## 7. Escalation conditions
- Trigger list:
- Escalation package required fields:
- Escalation budget and logging requirements:

## 8. Termination rules
- Normal termination conditions:
- Exceptional termination conditions:
- Cleanup and final handoff requirements:
```

---

## Role catalog guidance (non-exhaustive examples)

When defining roles, use the durable roles named conceptually in `CORPORATE_MODEL.md` as guidance (e.g., research, repair, review, synthesis, finance, operations). For each, still create a contract using the template above and still bind it to:

- Policy Gate compliance
- human-reserved authority limits
- evidence-witness vs verified semantics
- escalation event discipline
- auditability requirements

---

## Auditability integration requirements

Every role contract MUST state:

- what audit artifacts it produces (IDs, pointers, record identifiers)
- how provenance is recorded
- what is appended vs superseded
- what must be human-readable after the fact

If a role cannot comply with auditability for a proposed future action, the role contract MUST mark that action as disallowed.

---

## Security and adversarial considerations (required minimal rules)

Role contracts MUST include:

- prohibition on attempts to bypass Policy Gate
- prohibition on “self-authorizing” verification of its own material actions
- explicit rule: disputed evidence is preserved and escalated rather than overwritten
- explicit rule: budgets prevent resource draining and trigger role stop behavior

---

## Compliance statement

A role contract authored using this document is compliant only if it:
- preserves all hard invariants from `CORPORATE_MODEL.md`
- does not claim implemented capabilities without naming the relevant module
- defines explicit authority, tools, budgets, memory scope, acceptance criteria, escalation conditions, and termination rules for that role
