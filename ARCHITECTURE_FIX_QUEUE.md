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

**Investigation, step 2 — the user-visible half, now measured.**

The first case did not truncate because `_DEFAULT_MIN_TOTAL = 8`
(`core/low_evidence_policy.py:57`) and it carried three chunks. With ten
unverified chunks and a long answer, enforcement fires for real:

```
                       healthy                     broken
body                   1291 -> 460 chars           1291, unchanged
events                 verification_explained      verification_explained
                       answer_enforcement
                       low_evidence_truncation
first line to the user "Conclusion: no claim        "Conclusion: the API returns
                        could be backed by          42 on every call and has
                        the sources gathered        done since 2019
                        this cycle."                [general-knowledge]."
```

**So the damage to the answer is real and is the worst shape available.** The
honest refusal the enforcement wrote is replaced by a confident factual claim
the evidence does not support — and the user has no way to tell, because the
two events that would have said so are the ones that went missing.

The original draft is returned whole: nothing is partially rewritten, so the
failure is not visible as corruption either. It looks like an ordinary answer.

*Still to do before any fix:* the handler covers six operations, so the test
must be parameterised over each failure point. Fixing the exception in
`apply_answer_enforcement` alone would leave `draft.set_body()` free to keep
turning a break into a success.

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

**Done 2026-08-05.** Reproduced first, and the two hid differently. Referent
resolution: healthy logs `referent_decision` and sets a decision, broken logged
NOTHING and left it `None`, so the local-critique path silently never engaged.
Assumption extraction hid better — healthy registered 2 assumptions, broken 0,
and **no event either way**, because `assumptions_registered` fires later and
only when the registry is non-empty. A crash therefore produced exactly the
silence of a question with nothing to assume.

`_sensor_failed` declared in the host contract (that was the root cause: the
cure existed, the connection did not) and called from both. The referent
handler keeps `last_referent_decision = None` — reporting and failing safe are
different jobs, and the old code did only the second.

Cover: `tests/test_turn_context_failures_are_reported.py`, 8 tests, **3 of them
red on the old code**. Includes the reverse case — a question with nothing to
assume must stay silent, or one indistinguishable pair is traded for another.
- [x] done

### A4. Silent fallback of the synthesis contract

- **Where:** `core/loop_synthesis.py:227-231`.
- **Wrong how:** a prompt-registry failure silently returns the built-in
  contract, directly under a comment saying the registry is read precisely so an
  override is not "silently ignored". An operator with a task-specific contract
  gets someone else's and never learns.
- **Same file, `:572`:** a failure to select the cheap model tier silently keeps
  the normal one, so the cheap path quietly stops being cheap.

**Done 2026-08-05.** Both fallbacks are correct and stay; only the silence went.

*Contract registry.* Measured mechanism, not a worry: `SYSTEM_ANSWER` requires
section headers, a task-specific contract need not, so a registry failure makes
`_synthesis_expects_contract_headers` read True where it should read False —
and the verifier then marks the answer `malformed_output` for headers that
contract never asked for. **A wrong verdict about the answer, caused by a
swallowed error about the prompt.** One correction on the way: a first
measurement ran without importing `core.loop`, found an empty registry and
briefly suggested the fallback fires every turn. It does not — `core/loop.py:127`
registers the key on import.

*Cheap tier.* The only trace was the ABSENCE of `cheap_path_synth_model`, which
reads exactly like a turn that never took the cheap path. `cheap_path_active`
stayed True while the run used the normal model.

Both now report through `_sensor_failed`, which needed declaring in the host
contract first — the same root cause as A3. The length ratchet then pushed the
registry branch out into `_resolve_synthesis_contract`, which let the test drop
its copy of the branch and call the shipped code instead.

Cover: `tests/test_synthesis_fallbacks_are_reported.py`, 9 tests, **3 red on the
old code**. Both reverse cases included: a healthy registry and a turn that
never took the cheap path must stay silent.
- [x] done

### A5. N full-file rewrites where one would do (Л10)

- **Where:** `core/loop_memory_read.py:165`, `core/loop_memory_write.py:451` —
  `persistent_store.update(...)` **inside a loop**.
- **Wrong how:** `update` is a full file rewrite under a lock
  (`core/persistent_memory.py:58-65`). Retrieving five records rewrites the file
  five times.
- **The corrected form already exists:** `core/loop_response_deciders.py:147-164`
  — "one load, all increments in memory, ONE rewrite", review round #294.

**Done 2026-08-05, and the root was deeper than two loops.** Measured on five
records: the loop performed **5 rewrites and wrote 25 rows** where a batch does
**1 and 5** — quadratic, not merely wasteful.

The three callers had not been careless. The store offered **no public bulk
update**: `update` rewrites per record and `save_many` APPENDS. So each of them
improvised, and the one that found the right shape first (review #294) had to
reach past the API into `_rewrite` to get it. One missing operation, three
workarounds, and a fix applied at one site while the class stayed open at the
other two — the same shape as Л10 itself.

`PersistentMemoryStore.update_many` is that operation: one lock, one rewrite,
unknown ids skipped rather than raising (hygiene can archive a record between a
caller's read and its write, and that must not fail a turn). All three callers
now use it, including the one that had been reaching past the boundary.

Cover: `tests/test_persistent_bulk_update.py`, 8 tests, **7 red on the old
code**. The quadratic measurement is kept as a test rather than as a claim, and
the last test guards the CLASS — no caller may loop over `update` or touch
`_rewrite` — because fixing three instances is what left this open the first
time.

Alongside: the memory-credit block moved out of `_build_response_draft` into
`_credit_memory_records_used_in_the_answer`. The length ratchet asked for it and
the census had already said the same thing — it is memory accounting that landed
in a response builder because the verdict and the chain happened to be in scope.
220 -> 154 lines, ceiling lowered to bank it.
- [x] done

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

**Done 2026-08-05, and the measurement narrowed the item sharply.** The layer has
**20** such reads, not 3. Nineteen read a field `core/loop_init.py` sets at
construction, so their default can never fire — defensive habit, no hazard, and
rewriting them would be churn that blunts the rule for the case that matters.
**`assumption_store` from this item's own description is in that harmless
group**, and is deliberately left alone.

Exactly one field is neither set by the constructor nor reset per run:
`_synthesis_expects_contract_headers`. It is assigned ~370 lines into
`_synthesize`, after 44 calls that can raise, and the synthesis ladder catches
exceptions — so a turn whose synthesis broke early inherited the PREVIOUS turn's
value, and the `getattr` default hid it. Measured, wrong both ways:

```
turn 1 task-specific -> False ; turn 2 generic, synthesis raises early
   -> reads False -> a genuinely malformed answer is NOT flagged
turn 1 generic -> True ; turn 2 task-specific, synthesis raises early
   -> reads True  -> a correct table-only answer IS flagged malformed
```

Fixed by making it per-run state like its neighbours: reset to `True` in
`_run_inner` (the safe side — a contract never established is judged by the
generic one), declared in both readers' host contracts, read directly.

Cover: `tests/test_cross_mixin_fields_are_guaranteed.py`, 5 tests, **4 red on
the old code**. The rule is stated as a class — no `getattr` default may hide a
field that neither the constructor guarantees nor the run resets — and one test
asserts that the nineteen harmless sites stay allowed, so the guard cannot creep
into flagging everything and stop being read.
- [x] done

### A7. Twenty-eight silent handlers (Л9)

- **Wrong how:** of 59 `except` handlers in the layer **32 are silent**; four
  are the legitimate last-resort guard around logging itself. That leaves 28.
  MIR-077 is closed by a counter that measured **broad excepts**, not **silent
  bodies**.
- **Order:** fix the counter first (`scripts/except_audit.py` +
  `tests/test_except_audit_ratchet.py`) so it counts silence. It must **go red
  at 28**. Then close them one at a time.
- **A2, A3 and A4 are six of these 28**, so their fixes take part of the count.
- **Found while doing A4:** the audit does not recognise `_sensor_failed` as a
  report. It looks for a literal `.log(` call, so a handler that reports through
  the layer's own helper is scored silent and has to carry a justifying comment
  instead. That is the same "scope chosen by where someone looked" shape the
  counter itself is being fixed for, and it belongs to this item.

**Counter fixed 2026-08-05. The closing of the 17 that remain is NOT done.**

Two corrections, and the second is the point of the item.

*`_sensor_failed` now counts as reporting.* Fifteen handlers across `core/` were
scored silent while reporting correctly — which is how, in A4, a handler that
did report had to carry a justifying comment to satisfy the tool meant to find
silence.

*A second question, asked separately.* The existing ratchet asks whether a silent
handler carries a COMMENT, and stands at zero. All three defects the census
traced — A2, A3, A4 — satisfied it. **Every one was commented.** A comment helps
whoever reads the code and does nothing for an operator reading logs at three in
the morning. `journal_silent_in` asks the other question: how many handlers
write nothing to the journal? Two structural exclusions — a handler that
re-raises has reported by the strongest means there is, and one nested inside a
reporting handler is the guard around reporting itself.

**17 in the loop layer**, banked as a budget that may only shrink
(`tests/test_journal_silence_ratchet.py`). Not 28: A2–A4 closed six, and the
counting rule is stricter than the census's hand count. Verified red by adding
a silent handler — it names the address.

The number lives in the TOOL because two throwaway passes over the same layer
gave 22 and 18. One implementation, one number.

**Scope is the loop layer on purpose.** The same counter reports **281** across
all of `core/`, and calling those defects would be claiming past the
measurement — the exact mistake this exercise is about. Widening wants its own
evidence, one handler at a time, as the census did.

- [x] counter fixed and ratcheted
- [ ] the 17 closed one at a time

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

**Done 2026-08-05, first of Part B, per the operator's decision: extract the
shared core returning a result; iteration and non-iteration updates stay with
the callers; no mode parameter and no boolean flag.**

`AgentLoopEvidenceChain._catalogue_chain` computes `rank_chain` then
`knowledge_pipeline.run` and returns a `CatalogueResult`. It does **not** log and
does **not** store, because those are exactly what differs: the evidence-chain
caller skips the pipeline on the cheap path and logs a plain payload; the verify
caller runs inside the citation-fetch loop, stamps every event with `phase` and
`iteration`, writes into the run state as well, and quarantines conflicted
records afterwards. Folding those in would have needed the flag the decision
ruled out.

**The extraction proved itself before any test did.** The wiring guard went red:
five host attributes — `knowledge_pipeline`, `knowledge_auto_write`,
`source_registry_store`, `_knowledge_remember_batch`, `_unattended_run` — became
unused in `loop_verify_replan`. It had removed a coupling, not relocated a call.
Their declarations are gone.

A1 got stronger as a side effect: the loop layer now asks "who drives the run"
in **one** place instead of two, and the write-site count dropped 5 -> 4. The A1
tests were updated to pin exactly one, not at least one — a second site
reappearing is how that defect returns.

Cover: `tests/test_catalogue_core_is_shared.py`, 6 tests, red on the old code
(the module did not exist). The class is guarded, not the instance: only one
module in the layer may run the pipeline for a turn.
- [x] done

---

## Part C. Tests

### C1. Ninety-six files judge code by its source TEXT

- **Why first:** this class breaks on **any** file move, so it blocks all of
  Part B.
- One instance named: `tests/test_repair_routes_by_complexity.py`.

**Done 2026-08-05, and measuring shrank the item by an order of magnitude.**

The "96 files" was a crude count of anything calling `read_text`. Narrowed to
what the item is actually about — tests that break when a file MOVES — it is
**6 sites in 4 files**. Another **72 sites in 22 files** read source through
`module.__file__` or `inspect.getsource`; those follow the object and were never
the problem. So "96 files block Part B" was wrong: **one file did**, the one
pinning `core/loop_repair.py`, the very module B1 moves.

All six now find the source through the module. The blocking file gained a
`_propose_repair_source()` helper using `inspect.getsource`, which also removed
a quieter fault: it had been slicing text between `def propose_repair(` and
`def repair(`, so a method added between them would have silently widened what
three guards read.

Reading source is not forbidden — some invariants live only in the text (what a
module may import, whether a banned literal returned). What is forbidden is
coupling that check to a PATH, because a lawful move then reddens a test about
something else, and a test that fails for a reason it does not describe is worse
than no test.

Cover: `tests/test_no_test_pins_a_production_path.py`, 3 tests, red on the old
code. One of them caught ME: the first version searched for the literal string
and went red on the docstring recording what the file used to do — the
punishes-you-for-writing-down-history shape the census had already found in
`tests/test_one_task_store.py`. Judged by CALL now, not by text.

**Part B is unblocked.**
- [x] done

### C2. Mutation testing as the standing method

- Not "this test looks right" but "this test caught this break on this date".
  Run per class and keep the result beside the test.
- **Shown to work:** a test with no assertion at all went red on a deliberately
  broken path guard and green again after the revert.

**Done 2026-08-05.** `scripts/mutation_probe.py` breaks one thing at a time,
runs a chosen slice of the suite, and restores the file in a `finally`. It
refuses to start on a dirty working tree — an interrupted run must not be able
to lose work that was never committed — and refuses to start on a red selection,
because a survivor would mean nothing there.

**First real run: `core/low_evidence_policy.py`, ten mutations, SIX survived.**
That is the module deciding whether an answer is truncated for insufficient
evidence — the one whose failure (A2) hands a user a confident unsupported
claim. Its thresholds are largely unpinned:

```
low_evidence_policy.py:59   number 6 -> 7        _DEFAULT_UNVERIFIED_FLOOR
low_evidence_policy.py:85   boolean False -> True
low_evidence_policy.py:136  boolean True -> False
low_evidence_policy.py:172  number 3 -> 4
low_evidence_policy.py:173  number 3 -> 4
low_evidence_policy.py:208  comparison Eq -> NotEq
```

Recorded, not closed. Closing them is its own job, and naming them is what stops
it being forgotten.

**The probe corrected itself twice on its first outing**, and both are written
into it. It reported five survivors of which **three were parameter defaults**
every caller overrides — unobservable changes reported as gaps, and a probe that
flags what cannot matter stops being read. And the skip set it gained was built
from a SECOND parse, so it matched nodes by `id()` against a tree the mutator
never walked and did nothing at all — an inert guard reporting success, caught
before it ran and pinned by a test so it cannot return.

Cover: `tests/test_mutation_probe.py`, 9 tests, on synthetic sources so they
stay fast. The slow half — running the suite under a mutation — is what the
script does when a human asks.
- [x] done

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
