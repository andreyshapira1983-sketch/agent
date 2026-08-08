# What a `.qm` artifact is about — working note, Step 1

**Not a schema. Not adopted.** This records the candidate subject models, the
evidence that forced each, and the measurement that killed each. Every candidate is
kept, per the lab's rule that failed representations are results. **The ledger at
the bottom is the authoritative status; nothing above it states one.**

**Candidate identity.** Candidates are numbered **0** (file-centric), **1**
(single junction), **2** (boundary-and-carriers). No candidate ever existed between
1 and 2. A spoken report once called the last one "candidate 3" by counting the
dead ones and continuing from the wrong index; that was an off-by-one in the
telling, not a lost candidate. Recorded rather than silently corrected, because if
the identity of the thing under evaluation is ambiguous, so is every verdict about
it.

The labels above are **descriptions, not statuses** — deliberately. The roster
previously read `**2** (current)`, and when candidate 2 died the copy did not. The
fix is not to update the duplicate but to stop duplicating: status is asserted in
exactly one place, and `scripts/qm_doc_coherence.py` fails if a second appears.

## Why the question exists

`main.qm` is named after `main.py`. Of its 81 non-prose facts, **two** are about
`main.py`'s bytes (`subject.path`, `subject.sha256_prefix`). The rest describe
outcome states, an observer, and the internal structure of a different file in a
different language. The name and the content are about different things.

## Candidate 0 — file-centric: `filename ↔ semantic subject`

**FALSIFIED** before this step, by probe QT1: editing `cli/app.py` made a claim in
`main.qm` false while `main.py` stayed byte-identical (`def397b4c33e51d3` before
and after) and all eight cited tests stayed green. A subject whose truth is
controlled by files it does not name is not that file.

## Candidate 1 — the single junction: one carrier + the roles bound to it

Proposed in this step: the subject is one **carrier** (a channel holding
distinguishable states) crossing one **execution boundary**, with producer,
consumers, observers, consequences, validity and unproven scope attached as roles.

Motivation was real. Classifying all 81 facts against it left only 12 unslotted,
and the entry-point experiment had behaved exactly as it predicts: `main.py` is
neither carrier nor producer, and removing it destroyed no state and no observer —
all four baseline behaviours reproduced through `cli.app.run_cli`.

**FALSIFIED, in this step, by measurement.** Two runs were held equal on the
exit-code carrier and compared elsewhere:

| run | exit code | first line of stdout |
| --- | --- | --- |
| `python main.py --help` | 0 | `usage: main.py [-h] …` |
| `argv[0]='main.qm'`, `run_cli()` | 0 | `usage: main.qm [-h] …` |

One class on the exit-code carrier — indistinguishable to *every* observer of that
carrier, including `selfcheck.ps1`. Two classes on the stdout carrier: 2 of 33
lines differ. A subject with one carrier cannot hold the result of the very
experiment that motivated it.

## Specimen: this note contradicted itself, and a probe found it

Recorded before correcting, because the document became an instance of the disease
it is investigating.

**Two contradictions, verbatim as they stood.**

1. The opening roster read `**2** (current)` while the body read
   `## Candidate 2 — STRUCTURALLY INSUFFICIENT` and the ledger read *there is no
   surviving candidate*. One semantic fact, three assertions, one of them stale.
2. `Seven constraints (E1, E3–E7)` — that set has six members. The count is derived
   from the list, duplicated in prose, and has no authoritative source.

Both are the same shape: **one semantic fact → duplicated derived assertions →
the authoritative state changes → the dependents do not.**

**The detector, and its own two failures.** `scripts/qm_doc_coherence.py` is told
nothing about which sentence is stale and has no reference copy. It looks for one
subject carrying both an alive-class and a dead-class status word, and for a
spelled-out count whose own enumeration expands to a different size.

It was wrong four times, each differently, and every correction is a finding:

1. **Dead on arrival.** Line-scoped, it found nothing: the count `Seven
   constraints` ended one line and its enumeration `(E1, E3–E7)` began the next.
2. **Still blind to the named specimen.** Raised to sentences, it caught a
   contradiction — but by a different route, missing the roster, because
   `There are exactly three candidates:` and the bolded numerals fell into
   different sentences. Paragraph granularity fixed it.
3. **It lit up on its own evidence.** The specimen above quotes both
   contradictions verbatim, and the probe cannot tell **use from mention**. The
   document now marks quotations with backticks and the probe blanks code spans —
   blanking, not deleting, so line numbers survive.
4. **A recall hole the bite test exposed.** Injecting `Candidate 1 is the current
   surviving model` raised nothing, because candidate 1's death is asserted in a
   sentence that does not repeat its name. Status is inherited from headings, so
   the probe now scopes a heading's subject over its section.

5. **The use/mention fix survived one round and then broke.** Writing up failure 3
   produced quotations that *wrapped across lines*, and the span pattern forbade
   newlines inside a quotation, so the probe fired on its own evidence again. The
   fix had assumed quotations do not reflow.

Every one of those five is the same failure as the disease itself, one level down:
**the detector's scope was narrower than the fact it had to see** — and failure 5
adds that a fix can carry the same assumption it was written to remove.

**Bite test.** Clean document: `COHERENT`, exit 0. Inject two synthetic
contradictions: both rules fire, exit 1. Restore: exit 0 again. Run over
`docs/PROJECT_MAP.ru.md` as well: clean, so the disease was not everywhere — it
was where derived statements had drifted from a status that moved.

**Its limits, stated so its silence is not overread.** It detects *divergence
between duplicated assertions*; it has no notion of derivation and cannot say which
side is authoritative. It depends on the document marking quotations. Heading-scoped
inheritance is a heuristic, not a parse. Its vocabulary is fixed and small, so a
status word outside it is invisible.

**What this does and does not license.** It shows the requirement *can* be made to
bite. It does not establish the requirement as stated: the probe detects
**divergence between duplicated assertions**, and cannot tell which one is
authoritative — it has no notion of derivation at all. So what is earned is the
weaker claim that duplicated derived assertions can be made mechanically
detectable when they diverge. Generation-from-an-authoritative-state remains
**untested**, and is not adopted.

## Candidate 2 — STRUCTURALLY INSUFFICIENT

> **Killed by the projection measurement below.** It attached partitions and
> domains to *observation surfaces*. Measurement shows a partition is not a
> property of a surface: two projections on one surface induce different
> partitions, and one projection reaches several surfaces carrying one partition.
> A model whose ontology puts the partition in the wrong place cannot be repaired
> by wording. Preserved whole, as a failed representation.
>
> Its previous status line, `blocked on A2`, is withdrawn — it did not last long
> enough to be blocked. The authoritative status for every candidate is the ledger
> at the end of this note, and this block deliberately states none.

Its constraints are kept, because most of them outlived it. Scope is now separated
from ontology, which the first version of this table did not do:

| # | constraint | forced by | scope |
| --- | --- | --- | --- |
| E1 | the subject is not a file | QT1; 2 of 81 facts concern the named file | ontology |
| E2 | ~~the invoked file is a transit, replaceable without semantic loss~~ | — | **RESCOPED, see below** |
| E3 | a subject spans **several carriers**, each with its own state space | the `--help` probe | ontology |
| E4 | facts about **instantiation** fit no carrier or observer slot | the 12-fact residue | ontology |
| E5 | one consumer binds **several carriers** | `selfcheck.ps1:23–29` | ontology |
| E6 | the producer of record is a **symbol plus a termination contract**, not a file | the launch worked from `cli.app.run_cli` with `main.py` gone | ontology |
| E7 | a **behavioural partition is induced at the decision** — not by a surface, and not by a projection either | the two discriminators plus the two-consumer probe below | ontology |

### E2, rescoped

The old wording made a general claim out of one scenario. Split in two:

- **Measured, in one scenario, over two carriers:** with `argv[0]` held fixed,
  removing `main.py` from the execution path left the exit-code carrier and the
  stdout carrier byte-identical, and the `SystemExit` transit intact.
- **Ontology:** *transit* names a role that is invoked but is neither producer,
  carrier, consumer nor observer. **Whether that role is semantically empty is
  UNPROVEN.** Nothing licenses "replaceable without semantic loss" as a definition;
  the one law that said so is dead, and no carrier beyond those two was checked.

### E7 — projection is a distinct level, and it bites

The operator supplied two discriminators. Both were run, and both bite.

**(a) Two projections, one surface, different partitions.** `run_journal` returns
one dict through one call — a single output surface. Mutating only `ts` and
`trace_id`: `events_matched` stays 1 → 1 (**one class**), while `events` changes
(**two classes**). Same destination, same call, same keys. The surface cannot be
what carries the partition.

**(b) One projection, several surfaces, one partition.** In `selfcheck.ps1` the
projection `$code -eq 0` reaches three destinations — `return $ok`, the colour
choice `if ($ok)`, and the summary via `$results` — without its partition
changing.

So surfaces and projections are many-to-many. **But that is as far as those two
discriminators reach**, and the first wording — "the partition belongs to the
projection" — went further than they license.

**Third probe: one projection, two real consumers, incomparable partitions.** The
projection is *read the process exit code as an integer*. Two consumers already in
the repository take exactly that and nothing else:

| consumer | decision | partition over {0,1,2,3} |
| --- | --- | --- |
| `scripts/selfcheck.ps1:26` | `$ok = ($code -eq 0)` | `[[0], [1,2,3]]` |
| `tests/characterization/test_main_public_surface.py:154` | `assert result.returncode == 2` | `[[0,1,3], [2]]` |

Same projection, and **neither partition refines the other** — one merges 2 with 1
and 3, the other merges 0 with 1 and 3. A partition is therefore not determined by
the projection any more than by the surface.

What the three probes together establish: a projection is a *function from carrier
state to a value*; the behavioural partition is induced where that value meets a
**decision with a consequence**. So the chain is

> carrier state → projection → decision → consequence

and the partition lives at the third arrow. **Projection equivalence** (two runs
giving the same projected value) and **behavioural observer equivalence** (two runs
producing the same consequence) are different relations, and the first does not
determine the second.

### The terminology contradiction, resolved

The note said both *a partition is induced by an observer* and *a partition belongs
to a projection*. The two-consumer probe decides it: the first survives, the second
is **obsolete**. `projection` and `observer` are not interchangeable, and the
discriminator is exactly the probe above — hold the projection fixed, vary the
observer, and the partition changes.

### The law this model proposed — DEAD

> ~~Replacing a transit preserves every carrier's state space and every observer's
> partition.~~

**KILLED, twice over.** The first draft of this note recorded it as surviving
"except in one localised place". That is not what universal quantification means:
one counterexample on one carrier is a falsification, and the `usage:` line was
that counterexample. Attaching the exception afterwards was rescuing the law, not
testing it.

The second kill is worse for the law and better for the model. The entry
experiment **varied two factors at once** — it replaced the transit *and* changed
`argv[0]` — so it never tested the law at all. A 2×2 separating them:

| transit | argv[0] | stdout sha256 | first line |
| --- | --- | --- | --- |
| `main.py` executes | `main.py` | `7b32cf76287b` | `usage: main.py …` |
| `main.py` executes | `ZZZ` | `9fe0544188bb` | `usage: ZZZ …` |
| never touched | `main.py` | `7b32cf76287b` | `usage: main.py …` |
| never touched | `main.qm` | `fe1ceb4afe04` | `usage: main.qm …` |

Hold the transit and vary `argv[0]`: **different**. Hold `argv[0]` and remove the
transit entirely: **byte-identical**. So `argv[0]` is contributed by the
invocation, not by the transit; the transit's name appears there only because the
invocation names it. Of the operator's candidate readings, this measures one —
*`argv[0]` belongs to the binding* — and disconfirms *identity-bearing transit*:
the transit does not bear the identity, the binding supplies it.

**What is NOT thereby established.** That the transit is transparent in general.
Two carriers were compared in one scenario. The dead law is not resurrected in a
narrower form; there is currently no law, only one measured pair of runs.

**Vacuous cell, on record.** The first version of this 2×2 used
`runpy.run_path('main.py')` for the "vary argv[0]" cell. `run_path` replaces
`sys.argv[0]` with the path it is running for the duration of the call and
restores it afterwards, so the cell silently re-tied the two factors it existed to
separate, and returned the baseline hash. It was caught because a cell designed to
differ came back identical.

## Attacks

**A1 — is "boundary" just the file under another name?**
Named without reference to `main.py`: *one process invocation whose producer is
`cli.app.run_cli`*. The entry experiment proves this identifier is the load-bearing
one — the boundary survived the file's deletion. **SURVIVES**, with a recorded
consequence: boundary identity is parasitic on producer identity, so validity must
bind the producer's source. The current artifact binds `main.py` and
`scripts/selfcheck.ps1` and **does not bind `cli/app.py`** — which is precisely the
file QT1 used to falsify it. A demonstrated gap, not a hypothetical one.

**A2 — is the carrier set closed?** **UNRESOLVED — this is what blocks Step 1.**
The boundary emits at least: exit code, stdout, stderr, log files, memory writes,
and network calls to the model. If the subject is "all carriers", it can never be
completed, and certifying it is meaningless. A consumer-relative closure ("all
carriers with a named consumer") matches the lab's existing INSTANTIATED /
HYPOTHETICAL discipline, but nothing yet proves that closure is well-founded — a
new consumer can appear at any time and silently widen the subject. No probe has
been run against this.

**A3 — does the residue really need its own part, or is it a producer fact?**
P4 asserts six call sites, four passing `main.py`. That is neither producer nor
consumer alone; it is the consumer's invocation of the producer, and it names a
**transit** — an object E2 says carries no semantics but which must still be
nameable. **SURVIVES**, and it is what promoted "transit" from a description to a
part of the model.

**A4 — does the model earn its keep on the fact the old one could not express?**
The unexpressible fact was one consumer occupying two observer classes. Under
candidate 2 it becomes ordinary: one consumer, one carrier, two surfaces — plus a
second carrier the old model had no room for at all. **This attack was repelled at
the time, and its verdict is now superseded:** E7 shows the two things are two
projections, and that the partition was put on the surface. A4 is the attack that
was passed while the defect that later proved fatal was already present — kept as
a reminder that passing the attacks you thought to write is not evidence of
soundness.

## The A2 experiment — closure

**Scope, fixed before results.** Layer 1 is the **CPython audit-event frontier**,
not a kernel frontier: no kernel-level instrument was used, so anything a C
extension does without passing through CPython's hooks is invisible. Layer 2 is a
**hand-picked** set of in-process effects — environment mutation, module-table
growth, the outcome, the two text streams — and is explicitly *not* an enumeration
of in-process carriers: object mutation, queues, obligation state and planner
state were not observed. Layer 3 is a workspace filesystem diff. Nothing below
licenses "these are all the carriers of the boundary".

**Two paths through one boundary, three repeats each.**

| | channels | module reads | data reads | writes | stderr | outcome |
| --- | --- | --- | --- | --- | --- | --- |
| S1 `--file missing.txt` | `FS_READ` | 214 | 1 | 0 | 217 ch | RETURNED 2 |
| S2 `--ask :budget-status` | `FS_READ`, `FS_WRITE` | 220 | 2 | 1 | 6194 ch | RETURNED 0 |

Across three identical repeats the frontier was byte-stable in both scenarios —
which proves determinism, not closure. The result that matters is the comparison
*between* paths: **S2 has an entire channel S1 does not have**, and writes
`logs/run_<trace>.jsonl` — an effect this note first recorded as unconsumed and
later found to have had a consumer all along.

### H1 — split into three, because the first verdict was broader than the experiment

The original H1 wording covered two claims and the verdict prose drifted into a
third. Separated:

- **H1a — one path-independent active carrier set `C(B)`. REFUTED.** The active
  carrier set is a function of the path: S1 has `FS_READ` only, S2 adds an entire
  `FS_WRITE` channel. This is what the experiment actually measured.
- **H1b — closure derived by observing executions. REFUTED.** `FS_WRITE` was found
  only because S2 was chosen. A discovery procedure that finds what you already
  thought to look for is not a closure rule, and no number of additional runs
  changes that.
- **H1c — *any* mechanically checkable closed-world closure rule. UNPROVEN, NOT
  ATTACKED.** A union over paths, or a static, compositional, registry-based or
  formal derivation might yield one. Nothing here tests those, and the earlier
  phrasing "closing over all paths would require enumerating all paths" asserted
  their impossibility without evidence. **Withdrawn.**

### H2 — open-world with closed certification scope: UNTESTED

The decisive experiment was step 7: introduce one new consumer of a
previously-ignored effect and see whether the old certification becomes FALSE or
merely SCOPE_EXPANDED. **It was not run**, because the effect chosen for it turned
out to have a consumer already — see below. The result reached instead is
narrower, and is recorded at the end of this section.

### The partition belongs to the observer, not to the carrier

A first draft of this section said "stderr has the discrete partition". That is
wrong and the correction matters more than the observation. A carrier has a state
space; a **partition is induced on it by an observer**. The exact-byte observer
induces an almost-discrete partition over `stderr`, because nondeterministic
dimensions — `trace_id`, `ts` — put nominally identical runs in different raw byte
states. So:

> carrier state space ≠ observer partition
> raw physical distinguishability ≠ useful semantic distinguishability

Another observer over the same carrier may project those dimensions away and
induce a far coarser partition. That is not a workaround; it is what an observer
*is*.

**Both explanations for "identical input, different bytes" were attacked, and both
turned out to be real, in decomposable parts.** The first pair of runs was not
actually given identical input — they had different `--workspace` values, which
was my error, not a property of the system. Re-run with the workspace genuinely
held equal, the raw bytes still differ, and masking **the trace ids alone** makes
the two streams identical (timestamps and durations needed no masking in this
scenario). So: part of the difference was an omitted real input; the entire
residual is one nondeterministic dimension that byte-equality refuses to project
out.

### The journal already had a consumer — an unsearched absence, corrected

The draft above recorded "no named consumer reads it". **FALSE.**
`tools/agent_state_view.py:217` `run_journal()` reads the newest run log, filters
by event name, and returns a bounded result through the MCP server. It predates
this experiment by a long way — its own docstring cites MIR-077 and a review of
#317. The claim was an unsearched absence of exactly the kind the negative-claims
register exists to catch, and it was made in the same session that created that
register.

This settles one of the two questions asked of step 7: the effect did **not**
become semantic because a consumer appeared. It was already semantic and merely
**undiscovered by me**.

### The existing consumer, tested against the six criteria

Rather than invent a consumer, the real one was put through the test. `_LOGS` was
pointed at a laboratory copy of one run's journal; no production file was changed.

*Preconditions, proven before mutating:* the consumer really read the carrier
(`log_file` returned, `error` None), `rows_in_log=1`, `events_matched=1` for
`event_filter="session_start"` — so the thing to be observed demonstrably existed
in that run.

*Two opposite mutations, opposite predictions:*

| mutation | `events_matched` | `events` list |
| --- | --- | --- |
| only `ts` and `trace_id` changed | **1 → 1, unchanged** | **changed** |
| the `event` name changed | **1 → 0, RED as predicted** | changed |
| restored | **back to 1, GREEN** | restored |

The row count `rows_in_log` stayed 1 throughout the second mutation: the record
never left the carrier, only its class changed.

**The structural result: one consumer, one carrier, two projections of radically
different coarseness.** `events_matched` is a proper observer — blind to the
nondeterministic dimensions, and it bites on the distinction it claims to observe.
The `events` list, which carries `ts` and `payload`, is near-discrete on the same
carrier for the same consumer. Observer status is therefore a property of a
*projection*, not of a consumer and not of a carrier — a third level neither
candidate 1 nor candidate 2 has.

*What the consequence actually is, stated honestly:* the projected state changes
an authoritative **report** returned through a tool boundary. No in-process control
branch was shown to depend on it. That is weaker than `selfcheck.ps1`'s `$ok`,
which drives its own control flow, and the difference is recorded rather than
smoothed over.

### What this does and does not settle for H2

H2 remains **UNTESTED**. The experiment that would test it — a genuinely new
consumer appearing and the old certification being re-graded — did not happen,
because the consumer turned out to predate the experiment. What was measured
instead is narrower and still useful: a subject can be **incomplete without being
false**. Nothing `main.qm` asserts became untrue when the journal consumer was
found; the artifact simply never had a field able to say whether its carrier list
was complete or merely as far as anyone had looked.

### What H2 must actually test — and the one piece now built

A new consumer is **necessary but not sufficient**. The question H2 asks is whether
a validator can *detect* that the certification frontier moved, without a human
telling it. If a new consumer appears and the certification stays green because
nothing mechanically notices, then open-world certification is operationally
useless — the status would be produced by retrospective interpretation, which is
the thing this lab exists to avoid.

So H2 needs, in order: a **declared frontier** with checkable evidence; a **scoped
negative** for the chosen effect; a **genuinely new consumer**; and a **mechanism**
that emits FALSE / STALE / SCOPE_EXPANDED / still-valid-under-narrower-scope by
itself.

**The scoped negative is checkable — built and positively controlled.** The claim
form is *no consumer of effect E exists within declared universe U at snapshot S
according to procedure D*, not a metaphysical absence. Instantiated:

- **U** — 278 files: repository `.py` outside `tests/`, plus every `.ps1`.
- **D** — textual reference to the effect's store or its reader.
- **Positive control** — run against the journal effect, D returns 46 candidate
  files and **does find `tools/agent_state_view.py`**, the consumer this note
  previously missed. A search whose silence is to mean anything must first be shown
  capable of finding a known consumer; this one is.

**What the positive control does and does not license.** For a negative claim
**recall** is load-bearing and **precision** only costs review effort — 46 hits
needing a second, precise pass is expensive, not invalid. Recall itself is proven
for exactly one consumer: a reader that builds its path dynamically, lives outside
U, or is another process entirely would escape D. The negative is therefore valid
strictly at the scope declared, which is what makes it usable and what forbids
reading it as "nothing consumes E".

### Three vacuous probes, preserved

1. `runpy.run_path` cell in the 2×2 — silently re-tied the two factors it existed
   to separate. Caught because a cell designed to differ returned the baseline hash.
2. "the journal contains the `--reason` value" — the substring matched the word
   `reason` inside `model_routes`, not the value passed. `--reason` without
   `--expect` never reaches that record at all.
3. `payload.reason` as the new consumer's field — does not exist; the consumer read
   `None` in all four runs, so its "one class" verdict established nothing.
4. `model_routes` as the semantic field — stable under repetition, but blind to the
   escalation input (`--reason` + `--expect`), because routes are fixed at session
   start and escalation happens per call. A field that never moves proves nothing
   about the observer that reads it.
5. "two runs with identical input" — the two runs had different `--workspace`
   values. The control that was supposed to isolate nondeterminism was itself
   varying a real input.

Each was caught by the rule that a probe returning the convenient answer must
prove its subject existed in that run. Three of the five were caught only because
the answer came back *too clean*.

## Status

**No candidate is alive.** Candidates 0, 1 and 2 are all dead, each by a different
measurement, and all three are preserved above. Six constraints — E1, E3, E4, E5,
E6, E7 — outlived candidate 2 and are what any successor must satisfy; E2 is
rescoped to a measurement and is no longer a constraint.

**Step 1 is not complete. Step 2 does not begin.**

Ledger:

| item | status |
| --- | --- |
| H1a — path-independent active carrier set | REFUTED |
| H1b — closure by observing executions | REFUTED |
| H1c — any checkable closed-world rule | UNPROVEN, not attacked; earlier impossibility claim withdrawn |
| H2 — open world with closed certification scope | UNTESTED; the detection mechanism does not exist |
| scoped negative (universe U, snapshot S, procedure D) | **CHECKABLE**, positively controlled, recall proven for one consumer |
| projection as its own level | FORCED, both discriminators bite |
| transit — is the role semantically empty? | UNPROVEN; the law that asserted it is dead |
| producer-binding debt | **OPEN, and deeper than a missing hash** — see QT2 below |
| duplicated derived assertions detectable when they diverge | shown to bite; generation-from-authoritative-state untested |

### QT2 — the validity subject is not the producer's file either

QT1 proved that `cli/app.py` controls the truth of `main.qm`'s claims, so the
current binding is insufficient. It did **not** prove that binding `cli/app.py`'s
bytes would be sufficient. Separating experiment:

- precondition: `cli/app.py` sha `a39e32aae8070fbc`, baseline exit code 0;
- mutate **one downstream dependency**, `cli/one_shot.py`, one `return 0` → `return 3`;
- result: exit code **3**, so the claim *RETURNED payload ∈ {0,2}* is now **FALSE**;
- `cli/app.py` sha after: `a39e32aae8070fbc` — **unchanged**;
- therefore a `cli/app.py`-only binding would report **FRESH: falsely green**;
- restored, exit code back to 0.

So "producer source file" is not the validity subject. What controls the claim is
the set of code that can produce the values the claim quantifies over — which is
not known, is not a file, and is not to be approximated by hashing the repository.
Determining what evidence actually controls a claim is now part of Step 1, not a
detail of it.

The goal is no longer to rescue a candidate. It is to determine what exactly is
being certified, relative to which frontier, how that frontier is represented, and
how the system detects that it moved. Three of those four have no answer yet, and
the fourth — the frontier's representation — has one checkable piece.

Nothing here is adopted, and no field names are proposed. `main.qm` is unchanged,
and no production file was modified by any experiment in this note.
