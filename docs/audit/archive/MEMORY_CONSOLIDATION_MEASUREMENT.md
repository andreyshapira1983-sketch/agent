# Does the consolidation gap cost anything here? — measurement, 2026-08-22

MIR-128/129/130 record three gaps and the field's published approaches to them.
This document does the step the operator asked for after that: **check whether
the field's premise is actually true in this repository**, before anything is
built on it. Measurement only; nothing in production was changed.

Input: `data/episodic_memory.jsonl`, the live store, read as-is.

## 1. The loss is live, not theoretical

    rows in store : 200
    configured cap: 200

The store is **exactly at its ceiling**, so the FIFO is evicting right now.
Every new episode costs an old one. This had to be checked first: a gap that
never binds is a different priority from one that binds continuously.

## 2. The field's premise holds here — 38% of capacity is repetition

Counting distinct `(question, summary)` pairs:

    distinct pairs            : 124
    rows adding no new pair   :  76   = 38% of the store

Thirty-eight percent of a full store carries nothing that another row does not
already carry. That is the **measured ceiling** of what consolidation could
recover — not an estimate, and not a promise that a real implementation would
reach it.

## 3. The obvious fix is WRONG, and only the measurement shows it

The tempting implementation is to deduplicate by the question label, since 151
of 200 rows (75%) repeat a question. That number is a trap. Grouping by
question and counting distinct summaries inside each group:

    83 rows / 19 distinct summaries   self-build-produce
    20 rows / 15 distinct summaries   self-apply-run
    15 rows /  9 distinct summaries   self-task-produce
    10 rows / 10 distinct summaries   «назови один урок…»

The last row is decisive. Ten episodes share one question and **every one of
them is a different answer** — ten distinct pieces of experience. Deduplicating
by question would delete nine of them. The 75% figure and the 38% figure differ
by exactly this: the first counts labels, the second counts content.

**Conclusion that survives:** consolidation here must key on **content**, never
on the question label. Had this been implemented from the field's description
without the measurement, the first version would have destroyed real records
while reporting a large win.

## 4. Side finding, and it may matter more than the memory question

Of the 83 `self-build-produce` rows — 42% of the entire store — the outcomes
are:

    partial  65
    failed   10
    success   8

Nineteen distinct summaries across 83 attempts, dominated by repeated critic
vetoes (low confidence, unparsable builder replies) and a dirty working tree.
So the memory pressure is not general growth: **a single loop that mostly does
not succeed is eating the store**, and every distinct lesson elsewhere is
evicted to make room for another copy of its failure.

That reframes the priority. Consolidation would compress the symptom. The
repeating self-build failure is the cause, and it is visible here only because
memory is where it accumulates. Recorded as a finding, not acted on.

## 5. What this does NOT establish

- Not that consolidation should be built. The 38% is what it could recover, not
  a decision that recovering it is worth a new subsystem under the freeze.
- Not that a knowledge graph (MIR-129) is warranted; nothing here measured the
  cost of missing relations.
- Nothing about MIR-130. Cross-context consistency was not measured at all —
  this store holds answers, not the paired re-askings a consistency check needs.
- Not that the 200-row window is the wrong size. A larger window and
  consolidation are different fixes, and this measurement does not choose.
