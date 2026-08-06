# Self-Diagnosis & Self-Repair Doctrine

> **Authority.** This is a *normative specification* for how the agent reasons
> when the broken thing is **its own code, its own stored data, or its own
> invariant**. It is **subordinate to `docs/CENTRAL_AGENT_GOVERNANCE.md`**
> (which owns the Policy Gate / approval / budget contract): where the two
> overlap, the governance doc wins and this file only refines the repair
> specifics. Where this file and code disagree, **code wins and this file must
> be corrected**. File existence is never proof of implementation — the cited
> module is the proof.
>
> **Scope.** This document governs **self-diagnosis and self-repair only**. It
> is *not* a self-evolution mechanism: nothing here lets the agent change its
> own policy, amend its own doctrine, or merge its own work. Those remain human
> acts (§12).
>
> **Source of facts:** `core/self_repair.py`, `core/self_repair_models.py`,
> `core/self_repair_utils.py`, `core/self_build_memory.py`,
> `core/writer_completion.py`, `core/governance.py`,
> `scripts/completion_legacy_report.py`, `scripts/completion_backfill.py`,
> `scripts/migrate_completion_backfill.py`.
>
> **Relationship to `docs/self-audit-lessons.md`:** that file is a *historical
> record* of defect classes already found and the procedure for the next audit.
> This file is the *reasoning protocol* to apply when a new one appears. Neither
> replaces the other.

---

## How to read this document

Every section is marked:

* **NORMATIVE** — a rule the agent must follow when repairing itself. It binds
  reasoning, not any particular module.
* **IMPLEMENTED** — a rule that some code already enforces, with the module
  named. Enforcement means a machine will stop you, not that a document asked.
* **PLANNED** — stated intent with no enforcing code today.

A rule being NORMATIVE and not IMPLEMENTED is normal and honest. Claiming it is
IMPLEMENTED without a module is the failure this marking exists to prevent.

---

## 1. Prove the defect before repairing it — NORMATIVE

A repair may not begin from a suspicion. It begins from one of exactly two
things:

1. **A reproducible scenario** — a command, input, or test that makes the wrong
   behaviour appear on demand; or
2. **Durable runtime evidence** — a stored trace, log event, ledger row, or
   memory record that shows the wrong behaviour *already happened*.

"It looks wrong when I read the code" is a hypothesis, not a defect. Reading
carefully is how you *form* the hypothesis; it is never how you *close* it.

Where a fail-before test is possible, write it first and watch it fail for the
predicted reason. A test that passes before the fix has not proven anything; a
test that fails for a different reason has proven a different defect.

**IMPLEMENTED (partial):** `core/self_repair.py` refuses to apply a proposal
whose diagnosis is not verified (`_diagnosis_verified`) or whose confidence is
below `_DEFAULT_MIN_REPAIR_CONFIDENCE = 0.60`, and refuses an empty diff. It
does **not** verify that a reproduction exists — that remains the caller's duty.

---

## 2. Separate the symptom from the cause — NORMATIVE

The first call site that produces wrong output is a *symptom site*. Before
fixing it, enumerate the whole path:

* **producers** — every writer that can create this state;
* **consumers** — every reader that acts on it;
* **stores** — every place it is persisted, including legacy rows written by
  older code;
* **gates** — every admission, policy, or eligibility check it passes through;
* **entry points** — every production caller that can reach the mechanism.

Fixing only the observed caller leaves the same defect reachable by every other
caller. If the enumeration is incomplete, say so explicitly rather than implying
coverage that was not checked.

---

## 3. Mark the boundary of knowledge — NORMATIVE

Every claim in a repair must be classifiable into exactly one of four kinds:

| kind | means | may be acted on |
| --- | --- | --- |
| **stored fact** | present in a durable record or observed output | yes |
| **inference** | derived from stored facts by stated reasoning | yes, with the reasoning shown |
| **hypothesis** | plausible, not yet tested | only to design the next check |
| **unrecoverable** | the information was never written down | never |

Reporting an inference in the voice of a fact is a doctrine violation even when
the inference happens to be correct. Historical data that was never recorded is
**unrecoverable** and must be reported as such — not filled in.

---

## 4. Never reconstruct data by guess — NORMATIVE

Backfilling missing state into stored records is permitted **only** when one of
these holds:

1. **A durable classifier exists** — the record itself contains fields that
   decide the value with no ambiguity; or
2. **A writer signature is proved** — the record demonstrably came from one
   specific writer whose semantics are known, and the signature is composite
   enough that a similar record from another writer cannot match it.

A single category tag is a **category, not a provenance proof**. Anything that a
different writer, a diagnostic path, a manual edit, or a future writer could
also emit is not a signature.

Where neither holds, the correct outcome is to leave the record unclassified and
report the gap.

**IMPLEMENTED:** `scripts/completion_backfill.py` proves the writer signature
first and only then applies the shared mapping from `core/writer_completion.py`;
`scripts/completion_legacy_report.py` reports the same decision so diagnosis and
migration cannot disagree.

---

## 5. Fix the invariant at its boundary — NORMATIVE

An invariant belongs in **one** place: the boundary that owns it. When the same
rule is copied into several callers, some copy will drift, and the drift will be
invisible because every individual call site looks correct.

Concretely:

* a shared decision table lives in one module and is imported, not retyped;
* a writer settles its own verdict at banking time, rather than leaving readers
  to reconstruct it;
* a policy check lives at the gate, not in each caller that happens to remember
  to ask.

**IMPLEMENTED:** `core/writer_completion.py` is the single outcome→completion
state table; `core/self_build_memory.py` and `core/self_repair.py` settle the
verdict at write time rather than deferring it to readers.

---

## 6. Check that the mechanism is reachable — NORMATIVE

A module no production entry point calls is **not an implemented capability**.
It is a proposal that happens to compile.

Before claiming a capability exists, name the production caller. If the only
callers are tests, the honest statement is: *"the logic exists and is tested,
but nothing in the running system invokes it."* Moving such a module out of the
production package, or wiring it to a real entry point, are both acceptable
answers; pretending it runs is not.

---

## 7. Prove the fix on three levels — NORMATIVE

A repair is not finished until all three pass:

1. **Targeted regression** — the fail-before test now passes, and the tests that
   directly cover the changed unit pass.
2. **Full suite** — the whole test suite is green. A targeted-only run cannot
   see a contract you broke somewhere else.
3. **Real end-to-end flow** — the actual producer → storage → reader/consumer
   path is exercised against real state, not mocked state. A fix that satisfies
   unit tests but never demonstrates the real reader seeing the corrected value
   has not been shown to work.

Report which levels were run and which were not. An unrun level is a known gap,
not an assumed pass.

---

## 8. Test the negative cases — NORMATIVE

Positive tests prove the mechanism can fire. Negative tests prove it does not
fire on everything else, which is usually the more dangerous failure.

For every classifier, gate, or migration, add cases for:

* **similar** records that must NOT match;
* **corrupted** records — wrong types, missing keys, unexpected shapes;
* **ambiguous** records where two readings are possible;
* **future** records a later writer might emit.

All four must **fail closed**: on doubt, do nothing and report, rather than
apply the change. An unrecognised state is not an invitation to guess.

---

## 9. Migration safety requirements — NORMATIVE

Any script that rewrites stored records must provide **all** of:

* **dry-run by default** — applying requires an explicit flag;
* **a lock** — concurrent writers cannot interleave with the rewrite;
* **a backup** — the pre-migration state is recoverable, and its path is
  reported;
* **atomic write** — a crash mid-run leaves either the old or the new file,
  never a truncated one;
* **idempotence** — a second run changes nothing;
* **a post-migration report** — what changed, what was skipped, and what
  remains unresolved.

A migration that cannot be re-run safely cannot be trusted the first time.

**IMPLEMENTED:** `scripts/migrate_completion_backfill.py` provides all six.

---

## 10. Bank the lesson only after the verdict closes — NORMATIVE

A record of an attempt must not become reusable learning while the outcome is
still unknown. An unclassified lesson is worse than no lesson: it can be
replayed into future prompts carrying no verdict, teaching the agent that an
unfinished or failed attempt was a success.

Therefore the writer settles the completion verdict **at banking time**, from
the outcome it actually observed. Readers must never infer it later.

**IMPLEMENTED:** `core/self_repair.py::_write_repair_lesson` runs only for
`status == "repaired"` and stores `completion_state="achieved"` explicitly;
`core/self_build_memory.py` maps its observed outcome through the shared table.

---

## 11. Do not call an ordinary repair an evolution — NORMATIVE

These are three different classes of change and must be named accordingly:

| class | what it changes | who may approve |
| --- | --- | --- |
| **bug fix** | behaviour that was already meant to hold | normal review |
| **doctrine change** | how the agent is required to reason | human, deliberately |
| **architectural invariant change** | a rule other code depends on | human, with migration plan |

Fixing a defect is a bug fix even when the defect was interesting. Inflating it
into "the system evolved" destroys the vocabulary needed to describe the day
something actually does change class.

---

## 12. The human gate stays — NORMATIVE

Within this doctrine the agent **may**: investigate, reproduce, form and test
hypotheses, propose a patch, run tests, and explain what it found and how
confident it is.

The agent **may not**, without a human: merge, change policy or governance
rules, amend doctrine, or execute dangerous effects.

**IMPLEMENTED:** `core/governance.py` gates dangerous operations and
`core/self_repair.py` requests approval before writing and rolls back on red
tests. **Merge and doctrine amendment have no automated path at all**, which is
the intended design, not a missing feature.

---

## 13. What is implemented today vs planned

**IMPLEMENTED**

* Guarded repair transaction — diagnose → diff → approval → write → tests →
  rollback on red (`core/self_repair.py::SelfRepairController.run`).
* Refusal on unverified diagnosis, low confidence, or empty diff
  (`core/self_repair_utils.py`).
* Verdict settled by the writer at banking time
  (`core/writer_completion.py`, `core/self_build_memory.py`,
  `core/self_repair.py::_write_repair_lesson`).
* Signature-first legacy classification with a shared decision used by both the
  diagnostic report and the migration (`scripts/completion_backfill.py`,
  `scripts/completion_legacy_report.py`).
* Migration safety envelope — dry-run default, lock, backup, atomic write,
  idempotence, post-report (`scripts/migrate_completion_backfill.py`).
* Approval and policy gating of dangerous effects (`core/governance.py`).

**PLANNED (not in code)**

* Automatic defect detection from runtime evidence — nothing today notices its
  own misbehaviour and opens a repair on its own.
* Automatic authoring of a repair proposal — `core/self_repair.py` deliberately
  does not invent patches; a caller supplies the replacement content.
* Automated three-level verification — running the full suite and the real
  end-to-end flow is a human/CI act, not something the repair controller does.
* Automatic doctrine amendment or self-approved merge. **These are deliberately
  absent** (§12) and their absence is not a gap to be closed.

---

## 14. Checklist

Before claiming a self-repair is complete:

- [ ] The defect was reproduced, or durable evidence of it was cited.
- [ ] A fail-before test existed and failed for the predicted reason.
- [ ] All producers, consumers, stores, gates and entry points were enumerated.
- [ ] Each claim is labelled fact / inference / hypothesis / unrecoverable.
- [ ] No stored value was reconstructed without a durable classifier or a proved
      writer signature.
- [ ] The invariant lives at one boundary, not copied per caller.
- [ ] The production entry point that reaches the mechanism is named.
- [ ] Targeted tests, full suite, and a real end-to-end flow were run — or the
      unrun level was reported as a gap.
- [ ] Negative cases — similar, corrupted, ambiguous, future — fail closed.
- [ ] Any migration is dry-run by default, locked, backed up, atomic,
      idempotent, and reports afterwards.
- [ ] The lesson was banked only after the completion verdict closed.
- [ ] The change is named by its true class, and merge remains with the human.
