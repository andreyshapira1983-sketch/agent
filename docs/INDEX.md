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

## 2. Routing table

| I want to know… | Open | Authoritative for | **Not** authoritative for |
|---|---|---|---|
| What capabilities exist, and in what order they were built | [ROADMAP.md](ROADMAP.md) | intended order + per-track IMPLEMENTED/PARTIAL/PLANNED | defect status; per-PR daemon state |
| Which module does what | [AGENT_ANATOMY.md](AGENT_ANATOMY.md) | the `core/` module index (script-guarded) | whether a module is *wired* into a live path |
| Which operator commands exist | [COMMANDS_MAP.md](COMMANDS_MAP.md) | the `:command` surface + NL-routing parity | anything `main.py` added after this file's last sync |
| What the agent may do on its own vs. needs a human | [CENTRAL_AGENT_GOVERNANCE.md](CENTRAL_AGENT_GOVERNANCE.md) | the authority contract (Policy Gate, modes, approval, budget, human-reserved rights) | sub-agent specifics (→ next row) |
| How sub-agents are proposed, bounded, judged, retired | [SUBAGENT_LIFECYCLE.md](SUBAGENT_LIFECYCLE.md) | the sub-agent spec; subordinate to the governance doc | general approval/budget contract |
| **The status of any known defect** | [audit/MASTER_ISSUE_REGISTRY.md](audit/MASTER_ISSUE_REGISTRY.md) | **every issue status — the single owner; its tally is the authoritative count** (ask `python scripts/registry_tally.py --check`, never this page) | design of the fix (→ contract row) |
| How the deep revision is progressing | [audit/AUDIT_PROGRESS.md](audit/AUDIT_PROGRESS.md) | stage/group state, per-pass verification limits | per-issue status (registry owns it) |
| How memory actually flows (stores, reads, writes, dead sinks) | [audit/MEMORY_MAP.md](audit/MEMORY_MAP.md) | the read-only M0 census (§1–§13 = accepted baseline; §14 = labeled post-M0 addendum) | issue status; the target design |
| The **target** memory design | [audit/MEMORY_LIFECYCLE_CONTRACT.md](audit/MEMORY_LIFECYCLE_CONTRACT.md) | the canonical M1 design — **v2-draft, not approved, no code implements it** | what the code does today; the completion axis (see §4.5) |
| **Where the cognitive-memory work stands right now**, and the architectural thesis behind it | [audit/PROVIDER_AUDIT_CHECKPOINT.md](audit/PROVIDER_AUDIT_CHECKPOINT.md) | session state: what is decided, what is open, what was deliberately not done, and the discipline record of withdrawn claims. **Filename is historical** — it began as a provider-audit checkpoint | issue status; measured provider facts; the target design |
| What the provider/model layer can and cannot express (structured outputs, routing, streaming, refusal, truncation) | [audit/PROVIDER_STRUCTURED_OUTPUT_AUDIT.md](audit/PROVIDER_STRUCTURED_OUTPUT_AUDIT.md) | the measured provider-layer audit + its labeled dated addendum §A1–A9 on architectural placement | whether any model actually supports strict JSON Schema — **undetermined, no probe was run** |
| Daemon build state, item by item | [daemon-progress.md](daemon-progress.md) | per-sub-item implementation/PR/hotfix/acceptance | anything outside the daemon plan |
| Problem *classes* already fixed, and how to run the next audit | [self-audit-lessons.md](self-audit-lessons.md) | the 6 recurring anti-patterns + audit procedure. **History of regressions — never delete** | current defect status |
| How each document was classified, and where docs drifted from code | [audit/DOCUMENT_INVENTORY.md](audit/DOCUMENT_INVENTORY.md) | per-document classification as of Stage 1 | current code facts |
| Failure classes seen in *someone else's* system, as a checklist | [OPERATIONAL_FAILURE_MODES.md](OPERATIONAL_FAILURE_MODES.md) | the external OFM taxonomy — **no number in it measures this repo** | our defect status (see §4) |
| Long-horizon multi-agent org model | [future/CORPORATE_MODEL.md](future/CORPORATE_MODEL.md) | explicitly future/aspirational target | anything present-tense |
| The coordination-layer proposal (Agent Mail, Decision Log, …) | [MULTI_AGENT_COORDINATION_LAYER.md](MULTI_AGENT_COORDINATION_LAYER.md) | a self-declared **proposal**, not implemented | current behaviour |
| Behavioural doctrine / repo working rules | [../AGENT_DOCTRINE.md](../AGENT_DOCTRINE.md), [../AGENTS.md](../AGENTS.md) | correctness-first priority order; change discipline | technical facts |
| Entry point + source-of-truth hierarchy | [../README.md](../README.md) | navigation only | everything else |

### Historical audit sources (read for *method and context*, not for status)

These four found the defects and remain valuable as evidence and reasoning. Their
**status ledgers are superseded** by the registry — see §4.

- [CORE_AUDIT_2026-07-18.md](CORE_AUDIT_2026-07-18.md) — execution-verified core defects (CORE-01…12), plus honest negatives.
- [MEMORY_SYSTEM_AUDIT.md](MEMORY_SYSTEM_AUDIT.md) — memory-governance audit (MGA-01…09) + refined fix proposals (§D).
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

These are real and **not corrected in the source files**; they are listed so you
do not read a stale status as current. The registry wins in all four cases.

1. **CORE_AUDIT §7 says "None is yet fixed in code."** Stale. Several of its
   findings have since been fixed with named regression tests.
2. **OPERATIONAL_FAILURE_MODES §11 presents OFM-010 as the sole confirmed gap.**
   Stale — it maps to `MIR-017`, whose status the registry owns.
3. **MEMORY_FIX_PLAN A3 was not applied as written.** The code kept the `1.0`
   return and reframed it; the plan's "floor 0.3" is not what shipped.
4. **Counts drift between files.** Chronological log entries quoting "47 issues"
   are historical snapshots. Only the registry's tally is current.
5. **The M1 contract and the code now describe different lifecycle models, and
   the word `blocked` means two different things.** (Added 2026-07-21.) The
   contract's six dimensions contain **no completion axis**; `completion_state` /
   `declared_completion` (MIR-057) landed a day after it, and the contract's §11
   MIR-map does not know MIR-057. Meanwhile the contract's D2
   `verification_status` does not exist in code. And `blocked` is a terminal
   `usage_eligibility` state in the contract but a **completion** state in the
   code — two normative vocabularies, one token. Do not plan from either side
   alone until they are reconciled in one version bump.

Two more traps that are *by design*, not drift:

- OFM numbers describe an **external** operator's system. None of them measures
  this repo.
- `MEMORY_LIFECYCLE_CONTRACT.md` is a **v2-draft awaiting approval**. Nothing in
  it is implemented; do not read it as behaviour.

## 5. Housekeeping

- **`docs/audit/` — tracking status, corrected 2026-07-23.**
  `PROVIDER_STRUCTURED_OUTPUT_AUDIT.md` and
  `PROVIDER_AUDIT_CHECKPOINT.md` are now tracked in git. The earlier statement
  that these files were untracked is historical and no longer describes the
  current `main` branch.

- The canonical target-architecture text (`архитектура автономного Агента.txt`,
  repo root) is source-of-truth entry #2 per the README. **Read in full on
  2026-07-21** (previously recorded here as unread). Its §5 names *Capability
  Discovery & Negotiation* and *Tool Schema Drift Detection*; its §7 names
  *Capability Awareness* / *Limitation Awareness* / ODD — foreseen, per that
  document's own rule, is not implemented.

- Precedence everywhere: **current code → wired execution paths → reproducible
  tests → canonical docs.** When a document and the code disagree, the code wins
  and the document must be corrected.

