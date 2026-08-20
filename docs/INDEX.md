# Documentation Index — where to look, and who owns the answer

> **What this file is:** a routing map, created 2026-07-19. It answers *"which
> document should I open?"* and nothing else.
>
> **What this file is NOT:** a source of facts. It states no claim about code,
> no issue status, no capability. If you find a fact here, it is a bug in this
> file. Every row points at the document that owns the answer — go read that.
>
> **This file changes no other document.** The known contradictions listed in §4
> are recorded here as navigation warnings, not corrected in place.

## 1. The rule

**One question → one file that owns the answer.**

The doc set grew by accumulation: each new pass (audit, live probe, fix plan,
deep revision) created a *new* document instead of updating the existing one. So
the same problem can appear in four files with four different names and three
different statuses. The fix is not to delete history — it is to know, per
question, which single file is authoritative.

### 1.1 Which tree a document belongs to

The rule above says which file owns a question. This one says where that file
lives, and it is deliberately **not** "human reads one, the agent reads the
other" — the reader changes, the role of the artefact does not.

**`docs/` — description of the system**, for navigation, audit, operation and
history. A file being here does **not** make it a runtime input and does not
give it authority over the agent.

**`knowledge/` — material that is, or is intended to become, semantic input to
the agent's reasoning and decisions**: doctrine, learned knowledge, contracts,
generated knowledge.

And the part that matters most: **`knowledge/` is not "the agent's truth".** A
file there may be `future`, `proposal`, `generated`, `historical` or
`inactive`. Tidying the old disorder into a new source of false authority
would be worse than the disorder. So every file under `knowledge/` must have
four things knowable at a glance, and §6 states them for the whole tree:

| field | question it answers |
|---|---|
| **purpose** | which question this file owns |
| **status / authority** | is it binding on the agent today, and if not, what is it |
| **consumer** | who or what actually reads it — possibly nobody |
| **provenance** | who wrote it: operator, engineering pass, or the agent itself |

A file with no consumer is not automatically waste. It is a file whose
operational utility is **not established** — which is a different claim, and
the only one the evidence supports.

## 2. Routing table

| I want to know… | Open | Authoritative for | **Not** authoritative for |
|---|---|---|---|
| What capabilities exist, and in what order they were built | [ROADMAP.md](../knowledge/doctrine/ROADMAP.md) | intended order + per-track IMPLEMENTED/PARTIAL/PLANNED | defect status; per-PR daemon state |
| Project Intelligence package status (standalone vs wired) | [ROADMAP.md](../knowledge/doctrine/ROADMAP.md) (Track H) | whether the package exists and is integrated | scan/extractor/API/UI design details |
| Which module does what | [AGENT_ANATOMY.md](../knowledge/generated/AGENT_ANATOMY.md) | the `core/` module index (script-guarded) | whether a module is *wired* into a live path |
| **What the cognitive core is, which decisions only it makes, and which of its defences actually bite** | [COGNITIVE_CORE.md](COGNITIVE_CORE.md) | the boundary (core vs memory / runtime / tools / interfaces), the twenty-gate sequence, and each defence marked ENFORCING / OBSERVING / ABSENT **plus its measured recall** where one exists | issue status (registry owns it); the target memory design |
| Which operator commands exist | [COMMANDS_MAP.md](../knowledge/maps/COMMANDS_MAP.md) | the `:command` surface + NL-routing parity | anything `main.py` added after this file's last sync |
| What the agent may do on its own vs. needs a human | [CENTRAL_AGENT_GOVERNANCE.md](../knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md) | the authority contract (Policy Gate, modes, approval, budget, human-reserved rights) | sub-agent specifics (→ next row) |
| How sub-agents are proposed, bounded, judged, retired | [SUBAGENT_LIFECYCLE.md](../knowledge/doctrine/SUBAGENT_LIFECYCLE.md) | the sub-agent spec; subordinate to the governance doc | general approval/budget contract |
| **How the agent must diagnose and repair its own defects** | [SELF_REPAIR_DOCTRINE.md](../knowledge/doctrine/SELF_REPAIR_DOCTRINE.md) | the normative self-diagnosis/self-repair protocol (prove the defect → find the invariant boundary → migrate safely → verify on three levels → bank the lesson only after a closed verdict), with each section marked NORMATIVE / IMPLEMENTED / PLANNED. **Thematically routed — read only for repair/root-cause/regression/migration questions** | which defects exist (registry owns it); the historical list of past mistakes (→ self-audit-lessons.md) |
| **Which mistakes already cost us money and time, and how to find them in your own work** | [MISTAKE_NOTEBOOK.md](MISTAKE_NOTEBOOK.md) | mistakes caught on live runs: symptom, cost in numbers, a self-check, what to do | defect status (owned by `MASTER_ISSUE_REGISTRY.md`); behavioural rules (→ `AGENT_DOCTRINE.md`) |
| Daemon build state, item by item | [daemon-progress.md](daemon-progress.md) | per-sub-item implementation/PR/hotfix/acceptance | anything outside the daemon plan |
| How to run, operate, drive the HTTP API, and diagnose failures | [OPERATIONS.md](OPERATIONS.md) | run modes, API operator guide, troubleshooting, recovery | config values (→ CONFIGURATION.md) |
| How the agent is configured, and where it keeps state | [CONFIGURATION.md](CONFIGURATION.md) | env vars, `config/` files, `data/` state layout | issue status; runtime behaviour |
| Problem *classes* already fixed, and how to run the next audit | [self-audit-lessons.md](../knowledge/doctrine/self-audit-lessons.md) | the 6 recurring anti-patterns + audit procedure. **History of regressions — never delete** | current defect status |
| **How several models may argue without confirming each other's guesses** | [EVIDENCE_PROTOCOL.md](EVIDENCE_PROTOCOL.md) | **SPECIFICATION — nothing is built.** Roles with unequal rights, evidence types that exist only if an arbiter can reproduce them, freshness by world state AND instrument version, round limits, stopping criteria | anything about how the agent behaves today |
| Failure classes seen in *someone else's* system, as a checklist | [OPERATIONAL_FAILURE_MODES.md](OPERATIONAL_FAILURE_MODES.md) | the external OFM taxonomy — **no number in it measures this repo** | our defect status (see §4) |
| Long-horizon multi-agent org model | [CORPORATE_MODEL.md](../knowledge/doctrine/future/CORPORATE_MODEL.md) | explicitly future/aspirational target | anything present-tense |
| Behavioural doctrine / repo working rules | [AGENT_DOCTRINE.md](AGENT_DOCTRINE.md), [AGENTS.md](AGENTS.md) | correctness-first priority order; change discipline | technical facts |
| **Whose instruction wins when two contradict, and what the agent must do instead of choosing** | [INSTRUCTION_AUTHORITY.md](INSTRUCTION_AUTHORITY.md) | the six-level authority ranking (operator → task contract → test → repo invariant → convention → advisor), the rule that **any** conflict blocks, and the six-point conflict report. §1–§4 NORMATIVE, §5 IMPLEMENTED, §6 PLANNED | whether the gate is enforced on every write path — it is not (§6); ambiguity handling (→ AGENT_DOCTRINE.md) |
| Licence terms | [../LICENSE](../LICENSE) | proprietary, all-rights-reserved terms | — |

### Historical audit sources (read for *method and context*, not for status)

These four found the defects and remain valuable as evidence and reasoning. Their
**status ledgers are superseded** by the registry — see §4.

- [CORE_AUDIT_2026-07-18.md](CORE_AUDIT_2026-07-18.md) — execution-verified core defects (CORE-01…12), plus honest negatives.
- [MEMORY_SYSTEM_AUDIT.md](../knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md) — memory-governance audit (MGA-01…09) + refined fix proposals (§D).
- [LIVE_PROBE_FINDINGS.md](LIVE_PROBE_FINDINGS.md) — the only log grounded in *live runs* (LPF-001…018).
- [MEMORY_FIX_PLAN.md](MEMORY_FIX_PLAN.md) — the A1–A8 plan; partly executed, partly superseded by the M1 contract.

## 3. Issue IDs — one live prefix

`MIR-NNN` is the **only** live ID system. Every other prefix is a frozen alias
kept for traceability; the full alias mapping lives in the registry.

| Frozen prefix | Origin |
|---|---|
| `CORE-nn` | CORE_AUDIT_2026-07-18 |
| `MGA-nn` | MEMORY_SYSTEM_AUDIT |
| `LPF-nnn` | LIVE_PROBE_FINDINGS |
| `OFM-nnn` | OPERATIONAL_FAILURE_MODES (external checklist) |
| `A1…A8` | MEMORY_FIX_PLAN Part A |

Worked examples of the same defect under several names:

- `MIR-002` = CORE-03 = LPF-011 = A3 — quality `1.0` on an empty evidence chain.
- `MIR-003` = CORE-02 = MGA-03 = LPF-013 = A4 — one success mints an `active` procedure.
- `MIR-012` = CORE-09 = MGA-06 = A1 — `web_fetch` classified `private`.
- `MIR-017` = CORE-07 = OFM-010 — retry with no backoff.

**Do not mint a new prefix.** A new finding gets the next `MIR-` number.

## 4. Known contradictions — navigation warnings

These are real. The four historical audit docs (CORE_AUDIT, OPERATIONAL_FAILURE_MODES,
MEMORY_SYSTEM_AUDIT, LIVE_PROBE_FINDINGS) now each carry a dated **status-ledger
superseded** banner redirecting all status to the registry; the warnings below are kept
as a navigation aid. The registry wins in every case.

1. **CORE_AUDIT §7 says "None is yet fixed in code."** Stale; banner-annotated at
   source (2026-07-20). Several of its findings have since been fixed with named
   regression tests.
2. **OPERATIONAL_FAILURE_MODES §11 presents OFM-010 as the sole confirmed gap.**
   Stale; banner-annotated at source (2026-07-20). It maps to `MIR-017`, whose
   status the registry owns.
3. **MEMORY_FIX_PLAN A3 was not applied as written.** The code kept the `1.0`
   return and reframed it; the plan's "floor 0.3" is not what shipped.
4. **Counts drift between files.** Chronological log entries quoting "47 issues"
   are historical snapshots. Only the registry's tally is current.
5. ~~**The M1 contract and the code now describe different lifecycle models…**
   Do not plan from either side alone until they are reconciled in one version
   bump.~~ (Added 2026-07-21.) **RECONCILED 2026-07-28 — the blocker is lifted.**
   The differences were real and are now measured rather than warned about:
   `MEMORY_LIFECYCLE_CONTRACT.md` **§17** (v4-draft — v2's normative body
   unchanged, §17 added) checks all six dimensions against `core/` and reports,
   per dimension, what the code actually has and under what name. From that
   declaration scan: the completion axis exists in code and not in the contract,
   and **no per-record `verification_status` field exists** for D2 (verifier
   verdicts do exist, inside `VerificationReport` — the missing thing is a
   status stored on the record). Separately verified, not products of the scan:
   `blocked` carries two meanings, and the code's meaning occupies **14
   modules** (value-occurrence count) — which the original warning did not know.
   Four naming/ownership decisions came out of it and are open in §16 (D-3…D-6).
   *(2026-08-02: D-6's writer/boundary half is RULED — every new record gets an
   explicit completion verdict at the save boundary; see the contract's §16 D-6
   addendum and §17.6. Its envelope-membership half, and D-3…D-5, stay open.)*
   Planning may now proceed **from §17**, which states both sides; a decision
   that touches the still-open halves needs the operator first.

Two more traps that are *by design*, not drift:

- OFM numbers describe an **external** operator's system. None of them measures
  this repo.
- `MEMORY_LIFECYCLE_CONTRACT.md` is a **v4-draft awaiting approval**. Nothing in
  its normative body (§1–§16) is implemented; do not read those as behaviour.
  **§17 is the exception** and is the opposite kind of text: a measured report of
  what the code has today, added so the two sides could stop contradicting each
  other. Read §1–§16 as target, §17 as fact.

## 5. Housekeeping

- **Three files this section used to route no longer exist. Checked
  2026-08-20; the trace is kept because other text still points at them.**

  `docs/audit/PROVIDER_STRUCTURED_OUTPUT_AUDIT.md` and
  `docs/audit/PROVIDER_AUDIT_CHECKPOINT.md` — `docs/audit/` today holds
  `MASTER_ISSUE_REGISTRY.md` and, since 2026-08-20,
  `TEST_SUITE_AS_INSTRUMENT.md`.

  `архитектура автономного Агента.txt` (repo root) — this section called it
  **source-of-truth entry #2** and recorded it as *read in full on 2026-07-21*.
  It is **absent from the current tree and from git history**: it was never
  committed. **It must not be treated as authority, and no claim may cite it.**
  What still points at it, verified: the docstring of `scripts/gen_anatomy.py`
  and a fixture string in `tests/test_backlog_architecture_audit.py`. What does
  **not**: the live architecture audit, whose evidence list is
  `docs/AGENT_DOCTRINE.md`, `docs/COGNITIVE_CORE.md`, `README.md` — all
  present. So the dangling reference is prose, not a live authority path.

- Precedence everywhere: **current code → wired execution paths → reproducible
  tests → canonical docs.** When a document and the code disagree, the code wins
  and the document must be corrected.


## 6. Document ledger — every file, its authority and its consumer

The routing table above answers *"which file owns this question"*. This one
answers the three questions that decide whether a file may be believed:
**what it is, whether it binds the agent, and who actually reads it.**

Counted 2026-08-20: **37 documents, 20 920 lines.** Before this section the
routing table reached 22 of them; the largest document in the repository and
the most-cited one — `CODE_NOTES.md`, 3427 lines and 90 inbound references —
was not in it at all.

"Inbound" below is the number of files in the tree that name the document.
It measures attention, not worth: `ACCEPTANCE_LADDER.ru.md` has none and is a
frozen operator protocol.

### 6.1 `docs/` — description of the system (no authority over behaviour)

| file | owns | status | read by |
|---|---|---|---|
| `INDEX.md` | which file owns which question, and this ledger | active routing contract | anyone opening the docs |
| `CODE_NOTES.md` | why a change was made, with its live measurement | active reference; **binds nothing** | engineers and models arriving from a pointer in code (90 inbound) |
| `PROJECT_MAP.ru.md` | the project map for the operator, in Russian | active reference | operator |
| `audit/MASTER_ISSUE_REGISTRY.md` | defect status — the only live `MIR-` ledger | **authoritative for status** | engineering; the self-improvement signal gatherer |
| `audit/TEST_SUITE_AS_INSTRUMENT.md` | what the suite can and cannot falsify, measured by breaking working code | active record; **binds nothing** | whoever is about to trust a green run |
| `MISTAKE_NOTEBOOK.md` | mistakes caught live: symptom, cost, self-check | active reference | whoever is about to say "done" |
| `EVIDENCE_PROTOCOL.md` | how several models may argue without confirming each other | **specification — nothing is built** | design work only |
| `COGNITIVE_CORE.md` | the core boundary and its gates, proven from code | active reference | engineering |
| `daemon-progress.md` | per-item daemon build state | historical log | traceability |
| `OPERATIONAL_FAILURE_MODES.md` | an external operator's failure taxonomy | **no number in it measures this repo** | checklist use only |
| `ACCEPTANCE_LADDER.ru.md` | stage-1 self-report calibration, frozen after six operator amendments | operator protocol | operator; **0 inbound** |
| `LIVE_PROBE_FINDINGS.md` | what live runs showed (LPF-nnn) | evidence; **status superseded by the registry** | engineering |
| `MEMORY_FIX_PLAN.md` | the A1–A8 memory plan | partly executed, partly superseded | historical |
| `CORE_AUDIT_2026-07-18.md` | execution-verified core defects (CORE-nn) | evidence; **status superseded** | historical |
| `Технический_анализ_автономного_агента_и_функций_мозга.md` | one analysis note on the agent and brain functions | note | occasional |
| `NERVE_PROTOCOL.ru.md` | how to check that a nerve is really wired | active protocol | engineering |
| `CONFIGURATION.md` | env vars, `config/`, `data/` layout | active reference | operator, engineering |
| `INSTRUCTION_AUTHORITY.md` | whose instruction wins in a conflict | §1–§4 normative, §5 implemented, §6 planned | engineering |
| `OPERATIONS.md` | run modes, HTTP API, troubleshooting | active reference | operator |
| `AGENTS.md` | repository working guidelines | active convention | contributors |
| `AGENT_DOCTRINE.md` | behavioural doctrine, correctness-first order | active convention | contributors, models |
| `OPERATOR_NOTES.ru.md` | what documents the agent has created, for the operator | note | operator |

### 6.2 `knowledge/` — semantic input, present or intended

Presence here is not authority. The `status` column is the whole point.

| file | owns | status | read by |
|---|---|---|---|
| `doctrine/CENTRAL_AGENT_GOVERNANCE.md` | what the agent may do alone vs. what needs a human | **binding** | the agent's own gates; engineering |
| `doctrine/SUBAGENT_LIFECYCLE.md` | how sub-agents are proposed, bounded, retired | binding, subordinate to governance | sub-agent machinery |
| `doctrine/SELF_REPAIR_DOCTRINE.md` | the self-diagnosis/repair protocol | sections marked NORMATIVE / IMPLEMENTED / PLANNED | repair work |
| `doctrine/ROADMAP.md` | intended order of capabilities, per-track state | active | planning; charter goals |
| `doctrine/self-audit-lessons.md` | the recurring anti-patterns and the audit procedure | active; **never delete** | audits |
| `doctrine/MEMORY_SYSTEM_AUDIT.md` | the memory-governance audit (MGA-nn) | evidence; **status superseded** | historical |
| `maps/COMMANDS_MAP.md` | the `:command` surface and NL-routing parity | active, hand-maintained | operator, dispatch review |
| `generated/AGENT_ANATOMY.md` | the grouped `core/` module index | **generated** by `scripts/gen_anatomy.py`; a test fails if it drifts | navigation; backlog signals |
| `quantum/SUBJECT_MODEL.md` | what a `.qm` artifact is about | working note | that line of work |
| `doctrine/future/CORPORATE_MODEL.md` | the long-horizon multi-agent organisation | **future / aspirational** | design discussion |
| `doctrine/future/MEMORY_LIFECYCLE_CONTRACT.md` | the target memory lifecycle | **v4-draft awaiting approval.** §1–§16 target, §17 a measured report of today | planning, from §17 |
| `doctrine/future/MIGRATION_PATH.md` | how one would migrate to that target | draft / target | planning |
| `doctrine/future/AGENT_ROLE_CONTRACT.md` | proposed durable specialised roles | **DRAFT / TARGET, non-binding. Written by the agent's own charter campaign. 0 inbound, no runtime consumer** | nobody today |
| `doctrine/future/ORGANISATIONAL_ROLES_CONTRACT.md` | proposed role contracts and performance ledgers | **DRAFT / TARGET, non-binding. Written by the agent's own charter campaign. 0 inbound, no runtime consumer** | nobody today |

**On those last two, read 2026-08-20 (MIR-104).** Together they are 2282
lines the agent wrote about itself, and nothing in the tree reads them. Both
carry a DRAFT/TARGET banner. Their content was then examined against the
charter and the code, and the result inverts the expected worry:

* their seven "hard invariants" are **not proposals** — they restate
  `CENTRAL_AGENT_GOVERNANCE.md` §1–§9, where the same rules are marked
  IMPLEMENTED;
* the role contract they propose **exists** as `CanonicalSubagentContract`
  (memory / tool / budget scopes) in `core/subagent_contract.py`;
* the role-performance ledger of their §11 **exists** as `RoleRecord` in
  `core/subagent_registry.py` — per-role counters plus advisory scores;
* genuinely unbuilt, and this is what they are worth: durable role families
  as standing offices, assignment contracts with closure states, and the
  ledger refinements — segmentation by task class and risk tier,
  tamper-evidence, retention policy.

So the danger runs the other way. Read top-down under `future/`, they teach
that the agent has no role contracts and no performance ledger — and it has
both. A self-model that **understates** the system invites rebuilding what
exists. **Verdict: keep**; the unbuilt part is real and the restating part
is harmless once labelled. The smaller document is a subset of the larger on
invariants and is the merge candidate if one is ever wanted.

### 6.3 Outside both trees

| file | owns | status | read by |
|---|---|---|---|
| `README.md` | the entry point: what this is, how to run it | active | everyone (42 inbound) |
| `.github/instructions/codacy.instructions.md` | how Codacy's tooling must be invoked | tool instruction | the Codacy integration |
