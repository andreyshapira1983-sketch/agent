# Fix queue from the architecture census

A working list. Worked **top to bottom**, one item at a time, nothing skipped.

An item closes on three things: the change, a test that **fails on the old
code**, and the box ticked here. A test that was never red proves nothing —
that is the operator's standard and it applies to every item below.

When every box is ticked, `ARCHITECTURE_FILE_INVENTORY.md` is **deleted**, and
the findings that must outlive it move to `docs/MISTAKE_NOTEBOOK.md` and
`docs/audit/MASTER_ISSUE_REGISTRY.md`. Keeping the census after it has been
executed would leave two documents about one thing — which is exactly what it
found wrong in the code.

---

## Part A. Live defects — not structure, and they survive any reorganisation

These need no file moved. They go first.

### A1. The `require_verified` gate never reaches the turn path (Л8)

- **Where:** `core/loop_evidence_chain.py:257`, `core/loop_verify_replan.py:452`.
- **Wrong how:** an unattended run drives the ordinary cycle
  (`core/autonomous_runtime.py:947` calls `agent.run`), and neither of these
  knowledge-pipeline calls passes `require_verified`. The second sits **inside**
  the citation-fetch loop, so it fires once per iteration.
- **Why the guard missed it:** `tests/test_unattended_memory_writes.py` parsed
  the AST of `core.autonomous_runtime` alone.
- **Order:** widen the guard to every `knowledge_pipeline.run` in `core/` and
  make it **go red on today's code** — done. Only then decide what the value
  should be for the turn path, and decide it on "who drives the run", never on
  "which file the call sits in".
- [x] step 1 — guard widened, red, defect marked `xfail(strict=True)`
- [ ] step 2 — the value chosen and justified

### A2. Silent failure on the answer-truncation path

- **Where:** `core/loop_response_deciders.py:302-306`, a bare `except: pass`.
- **Wrong how:** it wraps the enforcement layer that **truncates an answer when
  the evidence does not support it**. If that throws, the user is handed the
  untruncated answer and nothing records that enforcement never ran.
- **The fix is already modelled in the same file:** three handlers write their
  own event and name the MIR-077 rule in the comment.

**Investigation, step 1 (2026-08-05) — nothing changed, findings only.**

The operator's instruction: find the exact handler, reproduce the exception, and
prove what the system loses — before touching anything.

*What the handler covers.* Six operations, not one: reading `last_source_ranking`
and `last_verification`, the `is_evidence_expected` policy call,
`apply_answer_enforcement` itself, the `answer_enforcement` journal write, the
conditional `low_evidence_truncation` write, and **`draft.set_body(...)` — the
truncation itself**. An exception in any of them lands in the same `pass`.

*Reproduced.* `apply_answer_enforcement` patched to raise, same input otherwise:

```
healthy : events = ['verification_explained', 'answer_enforcement']
broken  : events = ['verification_explained']
```

**The journal loses the only event that says enforcement ran.** So "enforcement
ran and decided no truncation was due" and "enforcement never ran" are the same
picture to any reader — the §21 shape, a check that did not run looking exactly
like one that passed.

*What is NOT recorded:* `_defect_signals` is never touched on this path, so the
episode carries no trace either. A run whose structural layer silently failed
banks as an ordinary one.

*Not yet proven, and not to be claimed until it is:* that a USER receives an
over-claiming answer. The constructed case did not truncate even on the healthy
path, so the damage is demonstrated for the journal and for the episode, and
**not** for the answer text. Closing that needs a case where enforcement really
truncates — the `insufficient_evidence` outcome with `applied=True`.

*Why the exception was thought acceptable:* the comment says truncation must
never take down the loop, and the fallback is "the original answer the user
would otherwise have got". That reasoning is sound about not crashing and silent
about not reporting — the same split MIR-077 was closed for.

**The decision this must feed, still open:** should this path fail closed, keep
the original answer, return the truncated answer with a defect signal, or end
the attempt as recovered/defective? Not to be answered before the user-visible
half is measured.

- [ ] done

### A3. Two silent failures in the turn context

- **Where:** `core/loop_context.py:215-217` (referent resolution),
  `core/loop_context.py:268-269` (assumption extraction).
- **Wrong how:** both swallow without a trace. The layer has `_sensor_failed`
  for exactly this, but it is **not declared** in this file's host contract, so
  the file cannot call it. Declare it and use it.
- [ ] done

### A4. Silent fallback of the synthesis contract

- **Where:** `core/loop_synthesis.py:227-231`.
- **Wrong how:** a prompt-registry failure silently returns the built-in
  contract, directly under a comment saying the registry is read precisely so an
  override is not "silently ignored". An operator with a task-specific contract
  gets someone else's and never learns.
- **Same file, `:572`:** a failure to select the cheap model tier silently keeps
  the normal one, so the cheap path quietly stops being cheap.
- [ ] done

### A5. N full-file rewrites where one would do (Л10)

- **Where:** `core/loop_memory_read.py:165`, `core/loop_memory_write.py:451` —
  `persistent_store.update(...)` **inside a loop**.
- **Wrong how:** `update` is a full file rewrite under a lock
  (`core/persistent_memory.py:58-65`). Retrieving five records rewrites the file
  five times.
- **The corrected form already exists:** `core/loop_response_deciders.py:147-164`
  — "one load, all increments in memory, ONE rewrite", review round #294.
- [ ] done

### A6. Hidden couplings through `getattr` with a default (Л6)

- **Where:** `_synthesis_expects_contract_headers` is read at
  `core/loop_verification.py:102` and `core/loop_verify_replan.py:402`;
  `assumption_store` at `core/loop_hygiene.py:175`.
- **Wrong how:** a third file sets the field, the readers do not declare it, and
  the default turns "no connection" into quiet behaviour under someone else's
  rule. For `_synthesis_expects_contract_headers` the default `True` also biases
  the verdict toward a false "malformed answer".
- **Check:** a test requiring every attribute read from a neighbouring mixin to
  be declared in the host contract, and forbidding `getattr` with a default on
  such fields.
- [ ] done

### A7. Twenty-eight silent handlers (Л9)

- **Wrong how:** of 59 `except` handlers in the layer **32 are silent**; four
  are the legitimate last-resort guard around logging itself. That leaves 28.
  MIR-077 is closed by a counter that measured **broad excepts**, not **silent
  bodies**.
- **Order:** fix the counter first (`scripts/except_audit.py` +
  `tests/test_except_audit_ratchet.py`) so it counts silence. It must **go red
  at 28**. Then close them one at a time.
- **A2, A3 and A4 are six of these 28**, so their fixes take part of the count.
- [ ] done

---

## Part B. Structure — each item needs its own confirmation first

### B1. Move out what the cycle never calls

- `core/loop_repair.py` — called by `cli/`, `core/self_repair.py`. No caller in
  `_run_inner` or any of its phases.
- `core/loop_hygiene.py` — called by `agent_tick.py:1146`,
  `cli/commands_memory.py`.
- `forget` / `list_persistent` from `core/loop_memory_commands.py` — CLI only.
- **Blocked by:** `tests/test_repair_routes_by_complexity.py` pins its rule by
  reading the file's **source text**. Moving the file reddens the test without
  touching behaviour. Rewrite it against behaviour first.
- [ ] done

### B2. One home for run state (Л3)

- **Five today:** `AttemptState` (21 in / 8 out), `SynthesisState` (13/2),
  `VerifyState` (18/4), ~25 fields on the agent instance, and plain locals in
  `_run_inner`.
- ~90 lines of `_run_inner` do nothing but move values between them.
- **The order is not optional:** this happens **before** any further extraction,
  or each new one costs a sixth state object.
- [ ] done

### B3. Cataloguing written twice

- `core/loop_verify_replan.py:442-486` repeats
  `core/loop_evidence_chain.py:243-270` instead of calling it.
- [ ] done

---

## Part C. Tests

### C1. Ninety-six files judge code by its source TEXT

- **Why first:** this class breaks on **any** file move, so it blocks all of
  Part B.
- One instance named: `tests/test_repair_routes_by_complexity.py`.
- [ ] done

### C2. Mutation testing as the standing method

- Not "this test looks right" but "this test caught this break on this date".
  Run per class and keep the result beside the test.
- **Shown to work:** a test with no assertion at all went red on a deliberately
  broken path guard and green again after the revert.
- [ ] done

### C3. Fifty-two files carrying 25+ tests

- `test_cli.py` 121, `test_assumption_registry.py` 107, `test_planner.py` 85 —
  check whether one file holds several unrelated subjects.
- [ ] done

---

## Deleted at the end

- `ARCHITECTURE_FILE_INVENTORY.md` — it will have done its job.
- This file, once every box is ticked.
- **Moving out and staying:** into `docs/MISTAKE_NOTEBOOK.md` — the rule that a
  check's scope gets chosen by where someone looked rather than by where the
  defect can be (three confirmations: Л8, Л9, Л10), and the rule that a doubtful
  conclusion is tested by changing the method of checking, never by reading it
  again (eighteen of my own errors caught that way in one session).
